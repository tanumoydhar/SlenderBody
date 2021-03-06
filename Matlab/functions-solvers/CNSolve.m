% Crank Nicolson / AB2 hybrid
function [Xp1,lambdas,fEstar,Xsp1] = CNSolve(nFib,N,Ms,Ks,Kts,I,wIt,FE,LRLs,URLs,Xt,...
    Xtm1,Xs,Xsm1,U0,dt,s0,w0,Lf,epsilon,Dmat,mu,xi,L,nonLocal,lamprev,maxiters,gravity,fext,strain)
    global Periodic;
    % Schur complement solve fiber by fiber
    Xp1 = zeros(3*N*nFib,1);
    Xsp1 = zeros(3*N*nFib,1);
    lambdas = zeros(3*N*nFib,1);
    fE = zeros(3*N*nFib,1);
    fEprev = zeros(3*N*nFib,1);
    nLvel = zeros(3*N*nFib,1);
    dU = zeros(3*N*nFib,1);
    l_m1 = 0*lambdas;
    l_m = lamprev;  % put in the previous lambda as the initial guess
    iters=0;
    reler = 10;
    % Precompute the relevant fE's 
    for iFib=1:nFib
        inds = (iFib-1)*3*N+1:3*N*iFib;
        fE(inds)=FE*(URLs \ (LRLs \ [eye(3*N); zeros(12,3*N)]*Xt(inds)));
        fEprev(inds)=FE*(URLs \ (LRLs \ [eye(3*N); zeros(12,3*N)]*Xtm1(inds)));
    end
    fEarg = 1.5*fE-0.5*fEprev;
    fEstar = fEarg;
    while ((nonLocal || iters==0) && reler > 1e-6 && iters < maxiters)
        if (iters > 0) % use CN for nonlocal also if doing fixed point
            for iFib = 1:nFib
                inds = (iFib-1)*3*N+1:3*N*iFib;
                fEarg(inds)=FE*(URLs \ (LRLs \ [eye(3*N); zeros(12,3*N)]*0.5*(Xt(inds)+Xp1(inds))));
            end
        end
        if (nonLocal)
            if (~Periodic)
                nLvel = MNonLocalUnbounded(nFib,N,s0,w0,Lf,epsilon,reshape(fEarg+l_m+fext,3,N*nFib),...
                     reshape(1.5*Xt-0.5*Xtm1,3,N*nFib)',reshape(1.5*Xs-0.5*Xsm1,3,N*nFib)',...
                     Dmat(1:3:3*N,1:3:3*N),mu);
            else
                nLvel = MNonLocalPeriodic(nFib,N,s0,w0,Lf,epsilon,reshape(fEarg+l_m+fext,3,N*nFib),...
                     reshape(1.5*Xt-0.5*Xtm1,3,N*nFib)',reshape(1.5*Xs-0.5*Xsm1,3,N*nFib)',...
                     Dmat(1:3:3*N,1:3:3*N),mu,xi,L,L,L,strain);
            end
            nLvel = reshape(nLvel',3*N*nFib,1);
        end
        for iFib=1:nFib % block diagonal solve
            grav = gravity;
            inds = (iFib-1)*3*N+1:3*N*iFib;
            M = Ms{iFib};   K = Ks{iFib};   Kt = Kts{iFib};
            B=[K-0.5*dt*M*FE*(URLs \ (LRLs \ [eye(3*N); zeros(12,3*N)]*K)) ...
                I-0.5*dt*M*FE*(URLs \ (LRLs \ [eye(3*N); zeros(12,3*N)]*I))];
            C=[Kt; wIt];
            D=[zeros(2*N-2) zeros(2*N-2,3); ...
                0.5*dt*wIt*FE*(URLs \ (LRLs \ [eye(3*N); zeros(12,3*N)]*K)) ...
                0.5*wIt*dt*FE*(URLs \ (LRLs \ [eye(3*N); zeros(12,3*N)]*I))];
            RHS = C*fE(inds)+C*fext(inds)+C*M^(-1)*U0(inds) + [Kt*repmat([0;0;grav/Lf],N,1); ...
                -wIt*fE(inds)+[0;0;grav]]+ C*M^(-1)*nLvel(inds);
            alphaU = lsqminnorm(C*M^(-1)*B+D,RHS);
            alphas=alphaU(1:2*N-2);
            Urigid=alphaU(2*N-1:2*N+1);
            ut = K*alphas+I*Urigid;
            dU(inds) = Dmat*(K*alphas);
            Xp1(inds) = Xt(inds)+dt*ut;
            Xsp1(inds) = Dmat*Xp1(inds);
            l_m1(inds) = l_m(inds);
            l_m(inds) = M \ (K*alphas+I*Urigid-nLvel(inds)-U0(inds))+...
                 -FE*(URLs \ (LRLs \ [eye(3*N); zeros(12,3*N)]*0.5*(Xp1(inds)+Xt(inds))))...
                 -fext(inds);
        end
        reler = norm(l_m-l_m1)/(max([1 norm(l_m)]));
        iters=iters+1;
        if (iters ==25 && reler > 1e-6)
            disp('Fixed point iteration not converging - change tolerance')
        end
    end
    dU = reshape(dU,3,N*nFib)';
    for iFib=1:nFib
        % Update with an inextensible motion
        inds = (iFib-1)*3*N+1:3*N*iFib;
        ginds = (iFib-1)*N+1:iFib*N;
        [newX,newXs] = updateX(Xt(inds),(Xp1(inds)-Xt(inds))/dt,N,dt,Lf,Xs(inds),Xsm1(inds),...
            dU(ginds,:));
        Xp1(inds) = newX;
        Xsp1(inds) = newXs;
    end
    lambdas=l_m;
end