function [x_opt,D_opt,cs,dt] = fcn_solver_gurobi(x_hist,D_init,dt_c,N_c,runtime_opt,D_min,D_max,MinOnTime,MinOffTime,x_lb,x_ub,x_init,cs_opt)
%Defines and solves MILP problem
% INPUTS:
%   x_hist      [:,1]       historic control variable
%   D_init      [duration]  actual duration level
% 
%   dt_c        [duration]  control time step
%   N_c         [1,1]       control horizon sample length
% 
%   runtime_opt [N_c,1]     requ. runtime of device per timestep, dimless
%   D_min       [duration]  minimum duration of device ("storage capacity")
%   D_max       [duration]  maximum duration of device ("storage capacity")
%   MinOnTime   [duration]  minimum on-time of device during operation
%   MinOffTime  [duration]  minimum off-time of device during operation
%   x_lb        [Nc,1]      lower bound of control variable      
%   x_ub        [Nc,1]      uber bound of control variable
%   x_init      [Nc,1]      initial value of control variable 
%
%   cs_opt      [N_c,1]     cost vector in €/timestep
%
% OUTPUTS:
%   x_opt       [N_c,1]     optimized control variable over horizon
%   D_opt       [N_c+1,1]   future duration level over horizon
%   cs          [1,1]       operation costs for total horizon in €
%   dt          [1,1]       optimization duration in sec
%
% VERSION 1.1
% 20241220, Florian Wenig, UniversityOfAppliedSciences Burgenland

%% Settings
params.outputflag = 0;

N_var = 3;  % variabletypes for each time step (x,risingedge,fallingedge)

%% Preprocessing
risingedge_hist = [0;diff(x_hist)==1];
fallingedge_hist = [0;diff(x_hist)==-1];
risingedge_init = [diff([x_hist(end,1);x_init])==1];
fallingedge_init = [diff([x_hist(end,1);x_init])==-1];
N_MinOn = MinOnTime/dt_c;
N_MinOff = MinOffTime/dt_c;

%% Define constraint matrices and objecitve function

% MILP constraint matrices in general: A*x<rhs
% 10 ... energy conservation (lower and upper comfort constraint)
% 21 ... determination equation for rising and falling edge
% 22 ... minimal on-time compliance
% 23 ... minimal off-time compliance

A_10 = [tril(hours(dt_c).*ones(N_c,N_c)),     zeros(N_c,(N_var-1)*N_c);...
        tril(hours(dt_c).*ones(N_c,N_c))*(-1),zeros(N_c,(N_var-1)*N_c)];
rhs_10 = [hours(D_max) - hours(D_init) + cumsum(runtime_opt*hours(dt_c));...
         (hours(D_min) - hours(D_init) + cumsum(runtime_opt*hours(dt_c)))*(-1)];
sense_10 = repmat('<',size(A_10,1),1);

A_21 = zeros(N_c,N_var*N_c);
rhs_21 = zeros(N_c,1);
j = 1;
A_21(j,0*N_c+(j-0))=+1;  
A_21(j,1*N_c+(j-0))=-1;
A_21(j,2*N_c+(j-0))=+1;      
rhs_21(1,1)=x_hist(end);
for j=2:N_c
    A_21(j,0*N_c+(j-0))=+1;  
    A_21(j,0*N_c+(j-1))=-1;
    A_21(j,1*N_c+(j-0))=-1;
    A_21(j,2*N_c+(j-0))=+1;      
end
sense_21 = repmat('=',size(A_21,1),1);

A_22 = zeros(N_c,N_var*N_c);
for j=1:N_c
    A_22(j,0*N_c+(j-0))=-1;
    k=j-N_MinOn+1;
    if k<=0
        rhs_22(j,1)=0; 
        for kk=k:0
            rhs_22(j,1)=rhs_22(j,1)-risingedge_hist(end+kk);  
        end
        for kk=1:j
            A_22(j,1*N_c+kk)=+1;
        end
    else
        for kk=k:j
            A_22(j,1*N_c+kk)=+1;  
        end
        rhs_22(j,1)=0;
    end
end
sense_22 = repmat('<',size(A_22,1),1);

A_23 = zeros(N_c,N_var*N_c);
for j=1:N_c
    A_23(j,0*N_c+(j-0))=+1;
    k=j-N_MinOff+1;
    if k<=0
        rhs_23(j,1)=1; 
        for kk=k:0
            rhs_23(j,1)=rhs_23(j,1)-fallingedge_hist(end+kk);  
        end
        for kk=1:j
            A_23(j,2*N_c+kk)=+1;
        end
    else
        for kk=k:j
            A_23(j,2*N_c+kk)=+1;  
        end
        rhs_23(j,1)=1;
    end
end
sense_23 = repmat('<',size(A_23,1),1);

model.A = sparse([A_10;A_21;A_22;A_23]);
model.rhs = [rhs_10;rhs_21;rhs_22;rhs_23];
model.start = [x_init;risingedge_init;fallingedge_init];
model.sense = [sense_10;sense_21;sense_22;sense_23];
model.obj = [hours(dt_c).*cs_opt;zeros((N_var-1)*N_c,1)];
model.lb  = [x_lb;zeros((N_var-1)*N_c,1)];
model.ub  = [x_ub;ones((N_var-1)*N_c,1)];
model.vtype = 'B';
model.modelsense = 'min';

%% Solve MILP problem
tic;
result = gurobi(model,params);
dt = toc;

%% Postprocessing results
x_opt = double(logical(result.x(1:N_c)));
D_opt = [D_init;D_init + cumsum(x_opt(1:end-1)*dt_c-hours(runtime_opt(1:end-1))*hours(dt_c))];
cs    = sum(cs_opt.*x_opt);

end
