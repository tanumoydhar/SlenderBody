import finufftpy as fi
import numpy as np
import numba as nb 
import RPYKernels as RPYcpp
import time
from math import pi

verbose = -1;

class RPYVelocityEvaluator(object):
    """
    This is a class that evaluates the velocity on N blobs due to N forces 
    The default is to do the "dumb" quadratic thing in free space - just 
    evaluating the total RPY kernel
    """
    ## ========================================
    ##          METHODS FOR INITIALIZATION
    ## ========================================
    def __init__(self,a,mu,Npts):
        """
        Input variables: a = hydrodynamic radius of the RPY blobs 
        (a = sqrt(3/2)*epsilon*L), mu = fluid viscosity
        """
        self._a = float(a);
        self._mu = float(mu);
        self._Npts = Npts;

    ## =========================================
    ##    PUBLIC METHODS CALLED OUTSIDE CLASS
    ## =========================================
    def calcBlobTotalVel(self,ptsxyz,forces,Dom,SpatialData,nThr):
        """
        Compute the total velocity of Npts due to forces at those pts. 
        Inputs: ptsxyz = Npts x 3 array of locations, forces = Npts x 3 array of forces,
        Dom = Domain object where the computation is done, SpatialData = spatialDatabase
        that has the array of points,nThr = number of OpenMP threads
        Ouputs: Npts x 3 array of the velocities at the Npts by calling the Numba function
        to do the free space computation
        """
        DLens = Dom.getPeriodicLens();
        for iL in DLens: # make sure there is no periodicity in the domain
            if iL is not None:
                raise NotImplementedError('Doing a free space velocity sum with periodicity in a direction');
        return RPYVelocityEvaluator.RPYKernel(self._Npts,ptsxyz,self._Npts,ptsxyz,forces,self._mu,self._a);
    
    @staticmethod
    @nb.njit(nb.float64[:,:](nb.int64,nb.float64[:,:],nb.int64,nb.float64[:,:],\
        nb.float64[:,:],nb.float64,nb.float64))
    def RPYKernel(Ntarg,Xtarg,Nsrc,Xsrc,forces,mu,a):
        """
        The dumb quadratic method to sum the RPY kernel. 
        Inputs: Ntarg = # of targets, Xtarg = Ntarg x 3 array of locations of targets, 
        Nsrc = # of sources, Xsrc = Nsrc x 3 array of locations of sources, forces = 
        Nsrc x 3 array of forces, mu = viscosity, a = RPY radius. 
        Output: the velocities at the targets
        """
        utot=np.zeros((Ntarg,3));
        oneOvermu = 1.0/mu;
        for iTarg in range(Ntarg):
            for iSrc in range(Nsrc):
                rvec = Xtarg[iTarg,:]-Xsrc[iSrc,:];
                r = np.linalg.norm(rvec);
                rhat = rvec/r;
                rhat[np.isnan(rhat)]=0;
                rdotf = np.sum(rhat*forces[iSrc,:]);
                if (r>2*a):
                    fval = (2*a**2 + 3*r**2)/(24*pi*r**3);
                    gval = (-2*a**2 + 3*r**2)/(12*pi*r**3);
                else:
                    fval = (32*a - 9*r)/(192*a**2*pi);
                    gval = (16*a - 3*r)/(96*a**2*pi);
                utot[iTarg,:]+= oneOvermu*(fval*forces[iSrc,:]+rdotf*(gval-fval)*rhat);
        return utot;


## Some parameters specific to Ewald
nearcut = 1e-3; # cutoff for near field interactions
fartol = 1e-3; # far field tolerance for FINUFFT
rcuttol = 1e-2; # accuracy of truncation distance for Ewald
trouble_xi_step = 0.1; # if we have to increase Ewald parameter xi mid-run, how much should we increase by?
class EwaldSplitter(RPYVelocityEvaluator):

    """
    This class implements Ewald splitting for the calculation of
    the non-local velocity on a TRIPLY PERIODIC DOMAIN
    """
    ## ========================================
    ##          METHODS FOR INITIALIZATION
    ## ========================================
    def __init__(self,a,mu,xi,PerDom,Npts):
        """
        Constructor. Initialize the Ewald splitter. 
        Extra input variables: xi = Ewald splitting parameter,
        PerDom = PeriodicDomain object, Npts = number of blobs
        """
        super().__init__(a,mu,Npts);
        self._xi = float(xi);
        self._currentDomain = PerDom;
        
        # Initialize C++ code
        RPYcpp.initRPYVars(a,mu,Npts,PerDom.getPeriodicLens());
        
        # Calculate the truncation distance for Ewald
        self.calcrcut();
        self.updateFarFieldArrays();
        self._ufarx = np.zeros([self._Npts],dtype=np.complex128);
        self._ufary = np.zeros([self._Npts],dtype=np.complex128);
        self._ufarz = np.zeros([self._Npts],dtype=np.complex128);
        print('1e-3 far field tolerance')
        
    ## =========================================
    ##    PUBLIC METHODS CALLED OUTSIDE CLASS
    ## =========================================
    def calcBlobTotalVel(self,ptsxyz,forces,Dom,SpatialData,nThr=1):
        """
        Total velocity due to Ewald (far field + near field). 
        Inputs: ptsxyz = the list of points 
        in undeformed, Cartesian coordinates, forces = forces at those points,
        SpatialData = SpatialDatabase object for fast neighbor computation.
        nThr = number of threads for parallel processing
        Output: the total velocity as an Npts x 3 array.
        """
        # Update domain object
        self._currentDomain = Dom;
        # First check if Ewald parameter is ok
        self.checkrcut();
        # Compute far field and near field
        t = time.time();
        Ewaldfar = self.EwaldFarVel(ptsxyz,forces,nThr); # far field Ewald
        if (verbose>=0):
            print ('Far field Ewald time %f' %(time.time()-t));
        t=time.time()
        Ewaldnear = self.EwaldNearVel(ptsxyz,forces,SpatialData,nThr); # near field Ewald
        if (verbose>=0):
            print ('Near field Ewald time %f' %(time.time()-t));
        #Ewaldnear2 = self.EwaldNearVelQuad(ptsxyz,forces,Dom); # near field Ewald quadratic
        #print('Near field error')
        #print(np.amax(Ewaldnear-Ewaldnear2))
        return Ewaldfar+Ewaldnear; 

    ## =========================================
    ##  PRIVATE METHODS ONLY CALLED WITHIN CLASS
    ## =========================================
    def EwaldFarVel(self,ptsxyz,forces,nThr):
        """
        This function computes the far field Ewald velocity. 
        Inputs: ptsxyz = the list of points 
        in undeformed, Cartesian coordinates, forces = forces at those points.
        This function relies entirely on calls to FINUFFT. See the documentation
        there for more information.
        """
        # Compute the coordinates in the transformed basis
        pts = self._currentDomain.primecoords(ptsxyz);
        # Rescale to [-pi,pi] (for FINUFFT)
        Lens = self._currentDomain.getPeriodicLens();
        pts = 2*pi*np.mod(pts,Lens)/Lens-pi;
        # Forcing on the grid (FINUFFT type 1)
        fi.nufft3d1(pts[:,0],pts[:,1],pts[:,2],forces[:,0],-1,fartol,\
                    self._nx,self._ny,self._nz,self._fxhat,modeord=1,nThreads=nThr);
        fi.nufft3d1(pts[:,0],pts[:,1],pts[:,2],forces[:,1],-1,fartol,\
                    self._nx,self._ny,self._nz,self._fyhat,modeord=1,nThreads=nThr);
        fi.nufft3d1(pts[:,0],pts[:,1],pts[:,2],forces[:,2],-1,fartol,\
                    self._nx,self._ny,self._nz,self._fzhat,modeord=1,nThreads=nThr);
        # Manipulation in Fourier space
        kxP, kyP, kzP = self._currentDomain.primeWaveNumbersFromUnprimed(self._kx, self._ky, self._kz);
        k = np.sqrt(kxP*kxP+kyP*kyP+kzP*kzP);
        # Multiplication factor for the RPY tensor
        factor = 1.0/(self._mu*k*k)*np.sinc(k*self._a/pi)**2;
        factor *= (1+k*k/(4*self._xi*self._xi))*np.exp(-k*k/(4*self._xi*self._xi)); # splitting function
        factor[0,0,0] = 0; # zero out 0 mode
        uxhat = factor * self._fxhat;
        uyhat = factor * self._fyhat;
        uzhat = factor * self._fzhat;
        # Project off so we get divergence free
        uprojx = uxhat-(kxP*uxhat+kyP*uyhat+kzP*uzhat)*kxP/(k*k);
        uprojx[0,0,0]=0;
        uprojy = uyhat-(kxP*uxhat+kyP*uyhat+kzP*uzhat)*kyP/(k*k);
        uprojy[0,0,0]=0;
        uprojz = uzhat-(kxP*uxhat+kyP*uyhat+kzP*uzhat)*kzP/(k*k);
        uprojz[0,0,0]=0;
        # Velocities at the points (FINUFFT type 2)
        fi.nufft3d2(pts[:,0],pts[:,1],pts[:,2],self._ufarx,1,fartol,uprojx,modeord=1,nThreads=nThr);
        fi.nufft3d2(pts[:,0],pts[:,1],pts[:,2],self._ufary,1,fartol,uprojy,modeord=1,nThreads=nThr);
        fi.nufft3d2(pts[:,0],pts[:,1],pts[:,2],self._ufarz,1,fartol,uprojz,modeord=1,nThreads=nThr);
        vol = self._currentDomain.getVol();
        return np.concatenate(([np.real(self._ufarx)/vol],[np.real(self._ufary)/vol],[np.real(self._ufarz)/vol])).T;
    
    def EwaldNearVel(self,ptsxyz,forces,SpatialData,nThreads=1):
        """
        Near field velocity. 
        Inputs: Npts = the number of blobs, ptsxyz = the list of points 
        in undeformed, Cartesian coordinates, forces = forces at those points,
        SpatialData = SpatialDatabase object for fast neighbor computation.
        nThreads = number of threads 
        Output: the near field velocity
        """
        # Find all pairs (returns an array of the pairs) within rcut
        neighborList = SpatialData.selfNeighborList(self._rcut);
        g = self._currentDomain.getg();
        # Call the C+ function which takes as input the pairs of points, number of points and gives
        # you the near field
        t=time.time();
        velNear = RPYcpp.EvaluateRPYNearPairs(neighborList,ptsxyz,forces,self._xi,g,self._rcut,nThreads);
        if (verbose>=0):
            print('Pairwise sum %f' %(time.time()-t))
        return velNear;

    def EwaldNearVelQuad(self,pts,forces,Dom):
        """
        Near field velocity. 
        Inputs: pts = the list of points 
        in undeformed, Cartesian coordinates, forces = forces at those points.
        Dom = domain object 
        This is the dumb quadratic method that does not use the SpatialDatabase
        class to speed up neighbor search.
        This is probably overkill since we already have a SpatialDatabase object
        that does the quadratic loops.
        """
        velNear = np.zeros((pts.T).shape);
        for iPt in range(self_Npts): # loop over points
            for jPt in range(self._Npts):
                # Find nearest periodic image (might need to speed this up)
                rvec = Dom.calcShifted(pts[iPt,:]-pts[jPt,:]);
                # Only actually do the computation when necessary
                if (np.linalg.norm(rvec) < self._rcut):
                    velNear[:,iPt]+=RPYcpp.RPYNearKernel(rvec,forces[jPt,:],self._xi);
        return velNear.T;

    def calcrcut(self):
        """
        Calculate the truncation distance for the Ewald near field. 
        We truncate the near field at the value rcut.
        """
        rcut=0;          # determine rcut
        Vatcut = np.abs(RPYcpp.RPYNearKernel([rcut,0,0],[1,0,0],self._xi));
        V0 = min(np.amax(Vatcut),1); # M0 is relative to the value at 0 or 1, whichever is smaller
        while (np.amax(Vatcut)/V0 > nearcut):
            rcut=rcut+rcuttol;
            Vatcut = np.abs(RPYcpp.RPYNearKernel([rcut,0,0],[1,0,0],self._xi));
        self._rcut =  rcut;
        print ('Ewald cut %f' %self._rcut)
    
    def checkrcut(self):
        """
        Check if rcut is less than one half period dynamically (the absolute
        length of a half period varies as the strain g changes). 
        If rcut is less than a half period, increase xi until rcut is less than a half
        period.
        """
        Lper = self._currentDomain.getPeriodicLens();
        try:
            Lmin = np.amin(Lper)/self._currentDomain.safetyfactor();
        except:
            raise NotImplementedError('Periodic velocity solver only implemented for triply periodic');
        vLover2 = np.amax(RPYcpp.RPYNearKernel([Lmin*0.5,0,0],[1,0,0],self._xi));
        if (vLover2 <= nearcut): # no interactions with more than 1 image
            return;
        print ('Need to increase xi or L, there are near interactions w/ more than 1 image');
        while (vLover2 > nearcut):
            # Modify xi
            self._xi+=trouble_xi_step;
            self.calcrcut();
            vLover2 = np.amax(RPYcpp.RPYNearKernel([Lmin*0.5,0,0],[1,0,0],self._xi));
            print('The new value of xi is %f' %self._xi)
            print('The new rcut %f' %self._rcut)
        # Update the far field arrays for the new xi
        self.updateFarFieldArrays();

    def updateFarFieldArrays(self):
        """
        Update/initialize the far field arrays when self._xi changes. 
        Method updates the self._waveNumbers on a standard 3-periodic grid and
        initialized the arrays for FINUFFT to put the forces.
        """
        Lens = self._currentDomain.getPeriodicLens();
        # Estimate the required grid
        gw=1.0/(2*self._xi); # width of the Gaussian
        h = gw/1.6;          # approximate grid spacing needed to resolve Gaussian
        nx, ny, nz = 2**(np.ceil(np.log2(Lens/h)));
        self._nx = int(nx); self._ny=int(ny); self._nz=int(nz); # number of grid points
        # Wave numbers (FFTW ordering) on UNDEFORMED COORDINATE SYSTEM
        kvx = np.concatenate((np.arange(self._nx/2),np.arange(-self._nx/2,0)))*2*pi/Lens[0];
        kvy = np.concatenate((np.arange(self._ny/2),np.arange(-self._ny/2,0)))*2*pi/Lens[1];
        kvz = np.concatenate((np.arange(self._nz/2),np.arange(-self._nz/2,0)))*2*pi/Lens[2];
        self._ky, self._kx, self._kz = np.meshgrid(kvy,kvx,kvz);
        # Prepare the arrays for FINUFFT
        self._fxhat = np.zeros([self._nx,self._ny,self._nz],dtype=np.complex128,order='F');
        self._fyhat = np.zeros([self._nx,self._ny,self._nz],dtype=np.complex128,order='F');
        self._fzhat = np.zeros([self._nx,self._ny,self._nz],dtype=np.complex128,order='F');
        self._fhat = np.zeros([self._nx,self._ny,self._nz,3],dtype=np.complex128,order='F');

