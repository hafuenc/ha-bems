%% Forecast based battery charging (FBC)
% based on pvprog algorithm from HTW Berlin:
% J. Bergner, J. Weniger, T. Tjaden : PVprog-Algorithmus - Algorithmus zur
% Umsetzung der prognosebasierten Batterieladung für PV-Speichersysteme mit
% messwertbasierten PV- und Lastprognosen (Version 1.1). Berlin: Hochschule
% für Technik und Wirtschaft HTW Berlin, 2016
% 
% key adaption compared to original pvprog:
% (1) use full "dc pv production forecast" 
%     instead of "ac pv production forecast - forecasted self-consumption"
%     due to use of dc-only avaiable power above inverter ac max power
% (2) correction algorithm is redesigned, error_ctrl is not used
%     Instead, the algorithm is switched-off and the maximum charging power
%     is allowed if the actual is below a certain thershold depending on
%     calender month

% GET current soc of battery
[soc_bat_init,~] = fcn_getHArestAPI_state(server_ha_local,token_ha,entity_id_soc_bat);

% define horizon for FBC (till end of day)
N_fbc = floor((dateshift(t_dat_VIE_opt(1,1),'end','day')-t_dat_VIE_opt(1,1))/dt_c);

% create horizon for FBC
t_dat_VIE_fbc = [t_dat_VIE_opt(1,1):dt_c:t_dat_VIE_opt(1,1)+dt_c*(N_fbc-1)]';

% resample dc production and ac consumption for FBC horizon
idx_fbc = find(t_dat_VIE_opt<=t_dat_VIE_fbc(end,1));
ep_pvs_dc_fbc = ep_pvs_dc_opt(idx_fbc);
ep_bui_ac_fbc = ep_dom_opt(idx_fbc)+ep_ufh_opt(idx_fbc).*x_ufh_opt(idx_fbc)+ep_dhw_opt(idx_fbc).*x_dhw_opt(idx_fbc);

% prepare synthetic feed-in limits in W
ep_synlim = repmat(0:10:max(ep_pvs_dc_fbc),length(t_dat_VIE_fbc),1);

% identify minimal synthetic feed-in limit  
% to fully charge battery untill sunset with energy above limit
[~,idx_synlim] = min(abs(sum(max(0,ep_pvs_dc_fbc/1000-ep_synlim/1000)*hours(dt_c))-(max(0,soc_bat_max-soc_bat_init))/100*battery_capacity/battereff));
ep_synlim_ident = ep_synlim(1,idx_synlim);
clear ep_synlim

% Calculate optimal battery charging power over horizon in W
ep_batcharge_fbc = round(max(0,ep_pvs_dc_fbc-ep_synlim_ident),1);

% Shut-Off charging limitation if soc is below minimum threshold
if soc_bat_init<soc_bat_limit_lookup(month(t_dat_VIE_now))
    ep_batcharge_fbc = ep_batcharge_max.*ones(size(ep_batcharge_fbc));
end

% Shut-Off charging limitation if all ep_pvs is used for battery charging
if round(ep_batcharge_fbc(1)) == round(ep_pvs_dc_fbc(1))
    ep_batcharge_fbc = ep_batcharge_max.*ones(size(ep_batcharge_fbc));
end

% POST maximum charging power threshold to battery (0 W<=ep<=5000 W)
try
    fcn_postHArestAPI_Number_SetValue(server_ha_local,token_ha,entity_id_ep_bat,max(min(ep_batcharge_fbc(1,1),5000),0));
catch ME
    fcn_postHArestAPI_Number_SetValue(server_ha_remote,token_ha,entity_id_ep_bat,max(min(ep_batcharge_fbc(1,1),5000),0));
end
