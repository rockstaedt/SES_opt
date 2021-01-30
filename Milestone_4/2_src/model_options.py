###############################################################################
### Model Options
###############################################################################

# This option enables a deterministic approach. If false then the stochastic
# approch is performed.
deterministic = True

# This option enables sensitivity analysis regarding the storage capacity from
# 0 to 20 kWh in steps of 4 kWh
sensitivity_analysis = False

# This option enables the output of result files, saved into '3_results'.
output = False

# This option enables the sampling using Crude Monte Carlo.
mc_sampling = True

# This option enables the sampling using the Antithetic Variates technique.
av_sampling = False

# This option enables the sampling using the Latin Hypercube Sampling technique.
lhc_sampling = False

# This option enables multiprocessing during the optimization.
multiprocessing = True

# This option enables the implementation of an electric vehicle.
ev = True