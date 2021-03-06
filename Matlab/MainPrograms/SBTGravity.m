% Main file for 4 falling fibers (example in our paper)
global Periodic flowtype doFP doSpecialQuad deltaLocal;
Periodic=0;
flowtype = 'S'; % S for shear, Q for quadratic
doFP = 0; % no finite part integral
doSpecialQuad=0; % no special quad
deltaLocal = 0.5; % part of the fiber to make ellipsoidal
makeMovie=1;
nFib=4;
nCL = 0;%nFib*(nFib-1)/2;
N=16;
Lf=2;   % microns
nonLocal = 1; % whether to do the nonlocal terms 
maxiters = 1;
Ld = 10; % periodic domain size (microns) not relevant here
xi = 0; % Ewald parameter not relevant here
mu=1;
eps=1e-3;
Eb=1;
dt=1e-3;
omega=0;
gam0=0; % no shear
t=0;
tf=0.25;
grav=-10; % for falling fibers, value of gravity
Kspring = 10; % spring constant for cross linkers
rl = 0.25; % rest length for cross linkers
% Nodes for solution, plus quadrature and barycentric weights:
[s, ~, b] = chebpts(N+4, [0 Lf], 2);
[s0,w0,~] = chebpts(N, [0 Lf], 1); % 1st-kind grid for ODE.
% Falling fibers
d=0.2;
fibpts=[d*ones(N,1) 0*ones(N,1) (s0-1); 0*ones(N,1) d*ones(N,1) (s0-1); ...
    -d*ones(N,1) 0*ones(N,1) (s0-1); 0*ones(N,1) -d*ones(N,1) (s0-1)];
D = diffmat(N, 1, [0 Lf], 'chebkind1');
[Rs,Ls,Ds,D4s,Dinv,LRLs,URLs,chebyshevmat,I,wIt]=stackMatrices3D(s0,w0,s,b,N,Lf);
FE = -Eb*Rs*D4s;
EvalMat=(vander(s0-Lf/2));
if (makeMovie)
    f=figure;
    movieframes=getframe(f);
end
Xpts=[];
forces=[];
links=[];
lambdas=zeros(3*N*nFib,1);
lambdalast=zeros(3*N*nFib,1);
fext=zeros(3*N*nFib,1);
Xt= reshape(fibpts',3*N*nFib,1);
Xtm1 = Xt;
Xst=[];
for iFib=1:nFib
    inds=(iFib-1)*N+1:iFib*N;
    Xst=[Xst;reshape((D*fibpts(inds,:))',3*N,1)];
    %Xst=[Xst;reshape(X_s(inds,:)',3*N,1)];
end
Xstm1=Xst;
stopcount=floor(tf/dt+1e-5);
saveEvery=1;
tic
fibstresses=zeros(stopcount,1);
gn=0;
SBTMain;