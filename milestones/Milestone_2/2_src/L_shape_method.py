import pyomo.environ as pyo
from pyomo.opt import SolverFactory
import time as tm
import numpy as np
import json
import pandas as pd

import helper

starting_time = tm.time()

###############################################################################
### Model Options
###############################################################################

# enables sensitivity analysis regarding the elecitricity price of the real
# time contract from 15 to 35 in steps of 5
sensitivity_analysis = False

# enables the output of csv files, saved into '3_results'
csv_output = False

# sample size for monte carlo simulation
sample_size = 10000

# set seed for randomness
seed = 12

###############################################################################
### Parameters
###############################################################################

# fixed generator costs in $/h
c1 = 2.12*10**-5

# linear generator costs in $/kWh
c2 = 0.128

# maximum capacity of generator
pmax = 12

# electricity price forward contract in $/kWh
l1 = 0.25

# electricity price real time contract in $/kWh
# this parameter is part of the sensitivity analysis that is why the
# parameter is defined as a list
if sensitivity_analysis:
    l2s = [0.15, 0.20, 0.25, 0.30, 0.35]
    # stopp a new time here to get the time period of the sensitivity analysis
    time_start_sens = tm.time()
else:
    l2s = [0.3]

# load values in kW
LOADS = helper.get_loads()

# monte carlo samples
SAMPLES = helper.get_monte_carlo_samples(LOADS, samples=sample_size, seed=seed)

# hours
HOURS = list(range(0, len(LOADS)))

# arbitrary value for convergence check
epsilon = 0.0001

###############################################################################
### L-shape method
###############################################################################

# solver for MIP
opt = pyo.SolverFactory('gurobi')

#------------------------------------------------------------------------------
# Helper functions
#------------------------------------------------------------------------------

def objective(u, p1, pg, p2):
    """
    This function calculates the objective value for all hours in hours. This is
    also the upper bound of the decomposition.
    """
    return sum(
        c1*u[h] + l1*p1[h] + c2*pg[h] + l2*p2[h] for h in HOURS
    )

def master_prob(u, p1, alpha):
    """
    This function calculates the lower bound of the decomposition.
    """
    return sum(c1*u[h] + l1*p1[h] + alpha[h] for h in HOURS)

# dataframe for computation times
times_dic = {'l2': [], 'time': []}

# loop over all real time prices and solve the L-shape method
for l2 in l2s:

    helper.print_sens_step(f'Solve L-Shape method for {l2} $/kWh')

    #---------------------------------------------------------------------------
    # Helper variables
    #---------------------------------------------------------------------------

    # list for the differences of the bounds
    bounds_difference = []

    # list for the objective values
    objective_values = []

    # list for lower bound values
    lower_bounds = []

    #---------------------------------------------------------------------------
    #---------------------------------------------------------------------------
    # Master problem
    #---------------------------------------------------------------------------
    #---------------------------------------------------------------------------

    master = pyo.ConcreteModel()

    # **************************************************************************
    # Sets
    # **************************************************************************

    # hour set
    master.H = pyo.RangeSet(0, 23)

    # **************************************************************************
    # Variables
    # **************************************************************************

    # unit commitment for generator
    master.u = pyo.Var(master.H, within=pyo.Binary)

    # electricity purchased with the forward contract
    master.p1 = pyo.Var(master.H, within=pyo.NonNegativeReals)

    # value function for second stage problem
    master.alpha = pyo.Var(master.H)

    # **************************************************************************
    # Objective function
    # **************************************************************************

    def master_obj(master):
        return sum(
            c1*master.u[h] + l1*master.p1[h] + master.alpha[h] for h in master.H
        )
    master.OBJ = pyo.Objective(rule=master_obj)

    # **************************************************************************
    # Constraints
    # **************************************************************************

    # alpha down (-500) is an arbitrary selected bound
    def alphacon1(master, H):
        return master.alpha[H] >= -500
    master.alphacon1 = pyo.Constraint(master.H, rule=alphacon1)

    #---------------------------------------------------------------------------
    # Initialization of master problem
    #---------------------------------------------------------------------------

    # save current time to get the time of calculating
    time_start = tm.time()

    # initialize iteration counter
    iteration = 0

    helper.print_caption('Initialization')

    print('Solving master problem...')

    helper.solve_model(opt, master)

    results_master = helper.get_results(master)

    #---------------------------------------------------------------------------
    #---------------------------------------------------------------------------
    # Sub problem
    #---------------------------------------------------------------------------
    #---------------------------------------------------------------------------

    sub = pyo.ConcreteModel()

    # **************************************************************************
    # Sets
    # **************************************************************************

    # hour set
    sub.H = pyo.RangeSet(0,23)

    # **************************************************************************
    # Variables
    # **************************************************************************

    # First stage variables

    # no need for declaration of variable types because that is determined by
    # corresponding variables of master problem
    sub.u = pyo.Var(sub.H)
    sub.p1 = pyo.Var(sub.H)

    # Second stage variables

    # electricity produced by generator
    sub.pg = pyo.Var(sub.H, within=pyo.NonNegativeReals)

    # electrictiy bought from retailer
    sub.p2 = pyo.Var(sub.H, within=pyo.NonNegativeReals)

    # **************************************************************************
    # Objective function
    # **************************************************************************

    sub.OBJ = pyo.Objective(
        expr=sum(c2*sub.pg[h] +l2*sub.p2[h] for h in sub.H)
    )

    # **************************************************************************
    # Constraints
    # **************************************************************************

    # load must be covered by production or purchasing electrictiy
    # take first random vector from samples
    pl = SAMPLES[0, :]
    def con_load(sub, H):
        return sub.pg[H] + sub.p1[H] + sub.p2[H] >= pl[H]
    sub.con_load = pyo.Constraint(sub.H, rule=con_load)

    # maximum capacity of generator
    def con_max(sub, H):
        return sub.pg[H] <= pmax*sub.u[H]
    sub.con_max = pyo.Constraint(sub.H, rule=con_max)

    # ensure variable u is equal to the solution of the master problem
    def dual_con1(sub, H):
        return sub.u[H] == results_master['u'][H]
    sub.dual_con1 = pyo.Constraint(sub.H, rule=dual_con1)

    # ensure variable p1 is equal to the solution of the master problem
    def dual_con2(sub, H):
        return sub.p1[H] == results_master['p1'][H]
    sub.dual_con2 = pyo.Constraint(sub.H, rule=dual_con2)

    #---------------------------------------------------------------------------
    # Initialization of sub problem
    #---------------------------------------------------------------------------

    # enable calculation of dual variables in pyomo
    sub.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)

    print('Solving sub problem for all samples...')

    results_sub = {}
    counter = 1
    for i, sample in enumerate(SAMPLES):
        counter = helper.print_status(counter, i)
        # filter for first sample because that is set in the initialization of
        # the model
        if i != 0:
            # get new load sample
            pl = sample
            # update constraint
            sub.con_load.reconstruct()
        # solve model
        helper.solve_model(opt, sub)
        results_sub[i] = helper.get_results(sub, dual=True)

    # check if upper and lower bound are converging
    converged, upper_bound, lower_bound = helper.convergence_check(
        objective,
        master_prob,
        results_master,
        results_sub,
        samples=SAMPLES,
        epsilon=epsilon
    )

    helper.print_convergence(converged)

    bounds_difference.append(abs(upper_bound - lower_bound))

    objective_values.append(upper_bound)

    lower_bounds.append(lower_bound)

    # optimize until upper and lower bound are converging
    while not converged:
        iteration += 1

        helper.print_caption(f'Iteration {iteration}')

        def cut(master, H):
            return (
                sum(
                    c2*results_sub[i]['pg'][H] + l2*results_sub[i]['p2'][H]
                    + results_sub[i]['dual_con1'][H]*(
                        master.u[H] - results_master['u'][H])
                    + results_sub[i]['dual_con2'][H]*(
                        master.p1[H] - results_master['p1'][H])
                    for i, sample in enumerate(SAMPLES)
                )/sample_size
                <= master.alpha[H]
            )

        setattr(master, f'cut_{iteration}', pyo.Constraint(master.H, rule=cut))
        print(f'Added cut_{iteration}')

        print('Solving master problem...')
        helper.solve_model(opt, master)
        results_master = helper.get_results(master)

        # update dual constraint in sub problem
        sub.dual_con1.reconstruct()
        sub.dual_con2.reconstruct()

        print('Solving sub problem for all samples...')
        results_sub = {}
        counter = 1
        for i, sample in enumerate(SAMPLES):
            # no if statement here because constraint con load is reconstructed
            # with the first sample in samples
            counter = helper.print_status(counter, i)
            pl = sample
            # update constraint
            sub.con_load.reconstruct()
            # solve model
            helper.solve_model(opt, sub)
            results_sub[i] = helper.get_results(sub, dual=True)

        converged, upper_bound, lower_bound = helper.convergence_check(
            objective,
            master_prob,
            results_master,
            results_sub,
            samples=SAMPLES,
            epsilon=epsilon
        )

        helper.print_convergence(converged)

        bounds_difference.append(abs(upper_bound - lower_bound))

        objective_values.append(upper_bound)

        lower_bounds.append(lower_bound)

    ############################################################################
    ### Results
    ############################################################################

    helper.print_caption('End Results')

    with open(f'../3_results/results_sub_{l2}.json', 'w') as outfile:
        json.dump(results_sub, outfile)

    with open(f'../3_results/results_master_{l2}.json', 'w') as outfile:
        json.dump(results_master, outfile)

    time_end = tm.time()
    times_dic['l2'].append(str(l2))
    times_dic['time'].append(time_end - time_start)
    print('Computation time:')
    print(f'\t{round(time_end - time_start, 2)}s')

    ############################################################################
    ### Exports
    ############################################################################

    if sensitivity_analysis:
        path = 'sensitivity analysis/'
        prefix = f'_sensitivity_{l2}.csv'
    else:
        path = ''
        prefix = '_no_sensitivity.csv'

    if csv_output:
        # export objective values, difference between bound into
        # '3_results' as CSV
        np.array(objective_values).tofile(
            '../3_results/' + path + 'objective_values' + prefix,
            sep = ','
        )
        np.array(upper_bound).tofile(
            '../3_results/' + path + 'upper_bounds' + prefix,
            sep = ','
        )
        np.array(lower_bound).tofile(
            '../3_results/' + path + 'lower_bounds' + prefix,
            sep = ','
        )
        np.array(bounds_difference).tofile(
            '../3_results/' + path + 'bounds_differences' + prefix,
            sep = ','
        )

if sensitivity_analysis:
    time_end_sens = tm.time()
    times_dic['l2'].append('ALL')
    times_dic['time'].append(time_end_sens - time_start_sens)
    print('Computation time for sensitivity analysis:')
    print(f'\t{round(time_end_sens - time_start_sens, 2)}s')

if csv_output:
    # save computation times as csv
    df_time = pd.DataFrame(times_dic)
    df_time.to_csv('../3_results/computation_times.csv', index=False)
    # save samples as csv
    df_samples = pd.DataFrame(SAMPLES)
    df_samples.to_csv('../3_results/samples.csv', index=False)


ending_time = tm.time()
print(ending_time - starting_time)