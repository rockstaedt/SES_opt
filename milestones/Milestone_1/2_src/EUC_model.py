import pyomo.environ as pyo
from pyomo.opt import SolverFactory
from pyomo.core import Var, value
import numpy as np
import pandas as pd

###############################################################################
### Model Options
###############################################################################

# enables sensitivity analysis regarding the elecitricity price from 5
# to 75 in steps of 10
sensitivity_analysis = True

# enables detailed output, recommended only without sensitivity analysis
detailed_output = False

# enables the output of csv files, saved into '2_results'
csv_output = True

###############################################################################
### Parameters
###############################################################################

# retail electricity price in €/kWh
if not sensitivity_analysis:
    lmbdas = [21.97/100]
else:
    lmbdas = np.arange(5,80,10)*0.01

# load values for 24 hours
pl = [8,8,10,10,10,16,22,24,26,32,30,28,22,18,16,16,20,24,28,34,38,30,22,12]
pl_sum = sum(pl)

# fuel cost parameters
c2 = np.array([1.2,1.12])*10**-3
c1 = np.array([0.128,0.532])
c  = np.array([2.12,1.28])*10**-5

# derive cost function for generator
price_generators_kwh = [c2[g]*2*1 + c1[g] for g in range(0,2)]

# min max power values
pmin = np.array([0,0])
pmax = np.array([20,40])

###############################################################################
### Model
###############################################################################

# create empty lists
objective_values = []
net_costs = []
# fuel costs is a dictionary of lists with key equal to generator index
fuel_costs = {
    0: [],
    1: []
}

# loop through lambdas
for lamda in lmbdas:

    # create concrete pyomo model
    model = pyo.ConcreteModel()

    #---------------------------------------------------------------------------
    # Sets
    #---------------------------------------------------------------------------

    # hourly set for 24 hours
    # (not from 1 to 24 because we use a python list for the load values)
    model.H = pyo.RangeSet(0,23)

    # generator set for the two generators
    model.G = pyo.RangeSet(0,1)

    #---------------------------------------------------------------------------
    # Variables
    #---------------------------------------------------------------------------

    # net power needed from external network per hour
    model.pn = pyo.Var(model.H)

    # power generation of each generator per hour, non negativity constraint
    model.pg = pyo.Var(model.H, model.G,within=pyo.NonNegativeReals)

    # binary unit commitment variable for each generator and hour
    model.u = pyo.Var(model.H, model.G, within=pyo.Binary)

    # helper variable to prevent quadratic solver problem
    model.y = pyo.Var(model.H, model.G, within=pyo.NonNegativeReals)

    #---------------------------------------------------------------------------
    # Objective Function
    #---------------------------------------------------------------------------

    # first part is net power cost with distribution company,
    # second part is fuel costs
    model.OBJ = pyo.Objective(
        expr=sum(lamda*model.pn[h] for h in model.H)
            + sum(
                (c2[g]*model.y[h,g] + c1[g]*model.pg[h,g] + c[g])*model.u[h,g]
                for h in model.H for g in model.G
            )
    )

    #---------------------------------------------------------------------------
    # Constraints
    #---------------------------------------------------------------------------

    # load for each hour
    def loadc(model, H):
        return sum(model.pg[H,g] for g in model.G) + model.pn[H] == pl[H]
    model.loadc = pyo.Constraint(model.H, rule=loadc)

    # minimum generation for each used generator and hour
    def minc(model, H, G):
        return model.u[H,G]*pmin[G] <= model.pg[H,G]
    model.minc = pyo.Constraint(model.H, model.G, rule=minc)

    # maximum generation for each used generator and hour
    def maxc(model, H, G):
        return model.u[H,G]*pmax[G] >= model.pg[H,G]
    model.maxc = pyo.Constraint(model.H,model.G, rule=maxc)

    # constraint for helper variable
    def hvc(model, H, G):
        return model.y[H,G] == model.pg[H,G]**2
    model.hvc = pyo.Constraint(model.H, model.G, rule=hvc)

    #---------------------------------------------------------------------------
    # Results
    #---------------------------------------------------------------------------

    opt = pyo.SolverFactory('gurobi')
    opt.options['NonConvex'] = 2

    results = opt.solve(model)

    model.solutions.load_from(results)

    if detailed_output:
        results.write()
        for v in model.component_objects(Var, active=True):
            print ("Variable", v)
            varobject = getattr(model, str(v))
            for index in varobject:
                print ("\t",index, varobject[index].value)

    if csv_output:
        data_dic_generator = {
            "Hour": [],
            "Generator": [],
            "Unit commitment": [],
            "Generation": []
        }
        data_dic_retailer = {
            "Hour": [],
            "Import/Export": []
        }
        for v in model.component_objects(Var, active=True):
            varobject = getattr(model, str(v))
            for index in varobject:
                if str(v) == "pn":
                    # fill dictionary for retailer
                    data_dic_retailer["Hour"].append(index)
                    data_dic_retailer["Import/Export"].append(
                        varobject[index].value
                    )
                elif str(v) == "pg":
                    # fill dictionary for generator
                    data_dic_generator["Hour"].append(index[0])
                    data_dic_generator["Generator"].append(index[1])
                    data_dic_generator["Generation"].append(
                        varobject[index].value
                    )
                elif str(v) == "u":
                    # fill generator dictionary with unit commitment value
                    data_dic_generator["Unit commitment"].append(
                        varobject[index].value
                    )

    # create dataframes
    df_retailer = pd.DataFrame(data_dic_retailer)
    df_generator = pd.DataFrame(data_dic_generator)

    # export dataframes into '3_results' as CSV
    df_retailer.to_csv(
        '../3_results/retailer_lambda_'
        + str(round(lamda, 4)) + '.csv',
        index=False
    )
    df_generator.to_csv(
        '../3_results/generator_lambda_'
        + str(round(lamda, 4)) + '.csv',
        index=False
    )

    # save objective value
    objective_values.append(pyo.value(model.OBJ))

    # calculate and save fuel costs and net costs
    net_costs.append(df_retailer['Import/Export'].sum()*lamda)
    for g in range(0,2):
        df_generator['Costs'] = df_generator.apply(
            lambda x:
                (
                    c2[g]*x.Generation**2 + c1[g]*x.Generation + c[g]
                )*x['Unit commitment'],
            axis=1
        )
        fuel_costs[g].append(
            df_generator[df_generator.Generator == g].Costs.sum()
        )

if sensitivity_analysis:
    prefix = '_sensitivity.csv'
else:
    prefix = '_no_sensitivity.csv'

# export objective values, fuel costs and net costs into '3_results' as CSV
np.array(objective_values).tofile(
    '../3_results/objective_values' + prefix,
    sep = ','
)
np.array(fuel_costs[0]).tofile(
    '../3_results/fuel_costs_generator1' + prefix,
    sep = ','
)
np.array(fuel_costs[1]).tofile(
    '../3_results/fuel_costs_generator2' + prefix,
    sep = ','
)
np.array(net_costs).tofile(
    '../3_results/net_costs' + prefix,
    sep = ','
)