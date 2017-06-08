import dolfin as df
import os
from . import *
from butter import unit_interval_filter, absolute
from common.io import mpi_is_root
__author__ = "Gaute Linga"

info_cyan("Welcome to the simple problem!")


class PeriodicBoundary(df.SubDomain):
    # Left boundary is target domain
    def __init__(self, Lx):
        self.Lx = Lx
        df.SubDomain.__init__(self)

    def inside(self, x, on_boundary):
        return bool(df.near(x[0], 0.) and on_boundary)

    def map(self, x, y):
        y[0] = x[0] - self.Lx
        y[1] = x[1]


class DirichletBoundary(df.SubDomain):
    def __init__(self, Ly):
        self.Ly = Ly
        df.SubDomain.__init__(self)

    def inside(self, x, on_boundary):
        return bool((df.near(x[1], 0.) or
                     df.near(x[1], self.Ly)) and on_boundary)


# Define solutes
# Format: name, valency, diffusivity in phase 1, diffusivity in phase
#         2, beta in phase 1, beta in phase 2
solutes = [["c_p",  1, 1., 1., 1., 1.],
           ["c_m", -1, 1., 1., 1., 1.]]

# Format: name : (family, degree, is_vector)
base_elements = dict(u=["Lagrange", 2, True],
                     p=["Lagrange", 1, False],
                     phi=["Lagrange", 1, False],
                     g=["Lagrange", 1, False],
                     c=["Lagrange", 1, False],
                     V=["Lagrange", 1, False])

factor = 1./4.

# Default parameters to be loaded unless starting from checkpoint.
parameters.update(
    solver="basic",
    folder="results_simple",
    restart_folder=False,
    enable_NS=True,
    enable_PF=True,
    enable_EC=True,
    save_intv=5,
    stats_intv=5,
    checkpoint_intv=50,
    tstep=0,
    dt=factor*0.08,
    t_0=0.,
    T=20.,
    dx=factor*1./16,
    interface_thickness=factor*0.040,
    solutes=solutes,
    base_elements=base_elements,
    Lx=1.,
    Ly=2.,
    rad_init=0.25,
    #
    V_top=1.,
    V_btm=0.,
    surface_tension=24.5,
    grav_const=0.98,
    #
    pf_mobility_coeff=factor*0.000040,
    density=[1000., 100.],
    viscosity=[10., 1.],
    permittivity=[1., 5.],
    #
    use_iterative_solvers=False,
    use_pressure_stabilization=False
)

constrained_domain = PeriodicBoundary(1.)


def mesh(Lx=1, Ly=5, dx=1./16, **namespace):
    return df.RectangleMesh(df.Point(0., 0.), df.Point(Lx, Ly),
                            int(Lx/dx), int(Ly/dx))


def initialize(Lx, Ly, rad_init,
               interface_thickness, solutes, restart_folder,
               field_to_subspace,
               enable_NS, enable_PF, enable_EC, **namespace):
    """ Create the initial state.
    The initial states are specified in a dict indexed by field. The format
    should be
                w_init_field[field] = 'df.Function(...)'.
    The work dicts w_ and w_1 are automatically initialized from these
    functions elsewhere in the code.

    Note: You only need to specify the initial states that are nonzero.
    """
    w_init_field = dict()
    if not restart_folder:
        # Phase field
        if enable_PF:
            w_init_field["phi"] = initial_phasefield(
                Lx/2, Lx/2, rad_init, interface_thickness,
                field_to_subspace["phi"].collapse())

        # Electrochemistry
        if enable_EC:
            # c_init = df.Function(field_to_subspace[solutes[0][0]].collapse())
            # c_init.vector()[:] = 0.
            # for solute in solutes:
            #     w_init_field[solute[0]] = c_init
            V_init_expr = df.Expression("x[1]/Ly", Ly=Ly, degree=1)
            w_init_field["V"] = df.interpolate(
                V_init_expr, field_to_subspace["V"].collapse())

    return w_init_field


def create_bcs(field_to_subspace, Lx, Ly, solutes,
               V_top, V_btm,
               enable_NS, enable_PF, enable_EC,
               **namespace):
    """ The boundary conditions are defined in terms of field. """
    bcs_fields = dict()

    # Navier-Stokes
    if enable_NS:
        #freeslip = df.DirichletBC(field_to_subspace["u"].sub(0),
        #                          df.Constant(0.),
        #                          "on_boundary && (x[0] < DOLFIN_EPS "
        #                          "|| x[0] > {Lx}-DOLFIN_EPS)".format(Lx=Lx))
        dbc = DirichletBoundary(Ly)
        noslip = df.DirichletBC(field_to_subspace["u"],
                                df.Constant((0., 0.)),
                                dbc)

        #bcs_fields["u"] = [noslip, freeslip]
        bcs_fields["u"] = [noslip]
        # GL: Should we pin the pressure?
        p_pin = df.DirichletBC(field_to_subspace["p"],
                               df.Constant(0.),
                               "x[0] < DOLFIN_EPS && x[1] < DOLFIN_EPS",
                               "pointwise")
        bcs_fields["p"] = [p_pin]

    # Phase field
    # if enable_PF:
    #     bcs_fields["phi"] = []
    #     bcs_fields["g"] = []

    # Electrochemistry
    if enable_EC:
        bc_V_top = df.DirichletBC(
            field_to_subspace["V"], df.Constant(V_top),
            "on_boundary && x[1] > {Ly}-DOLFIN_EPS".format(Ly=Ly))
        bc_V_btm = df.DirichletBC(field_to_subspace["V"], df.Constant(V_btm),
                                  "on_boundary && x[1] < DOLFIN_EPS")
        # bcs_fields["EC"] = [bc_V_top, bc_V_btm]
        # for solute in solutes:
        #     bcs_fields[solute[0]] = []
        bcs_fields["V"] = [bc_V_top, bc_V_btm]
    return bcs_fields


def initial_phasefield(x0, y0, rad, eps, function_space, shape="circle"):
    if shape == "flat":
        expr_str = "tanh((x[1]-y0)/(sqrt(2)*eps))"
    elif shape == "circle":
        expr_str = ("tanh(sqrt(2)*(sqrt(pow(x[0]-x0,2)" +
                    "+pow(x[1]-y0,2))-rad)/eps)")
    else:
        info_red("Unrecognized shape: " + shape)
        exit()
    phi_init_expr = df.Expression(expr_str, x0=x0, y0=y0, rad=rad,
                                  eps=eps, degree=2)
    phi_init = df.interpolate(phi_init_expr, function_space)
    return phi_init


def tstep_hook(t, tstep, stats_intv, statsfile, field_to_subspace,
               field_to_subproblem, subproblems, w_, **namespace):
    info_blue("Timestep = {}".format(tstep))

    if stats_intv and tstep % stats_intv == 0:
        # Seems like a rather awkward way of doing this,
        # but any other way seems to fuck up the simulation.
        subproblem_name, subproblem_i = field_to_subproblem["phi"]
        Q = w_[subproblem_name].split(deepcopy=True)[subproblem_i]
        bubble = df.interpolate(Q, field_to_subspace["phi"].collapse())
        bubble = 0.5*(1.-df.sign(bubble))
        mass = df.assemble(bubble*df.dx)
        massy = df.assemble(
            bubble*df.Expression("x[1]", degree=1)*df.dx)
        if mpi_is_root():
            with file(statsfile, "a") as outfile:
                outfile.write("{} {} {} \n".format(t, mass, massy))


def pf_mobility(phi, gamma):
    """ Phase field mobility function. """
    # return gamma * (phi**2-1.)**2
    func = 1.-phi**2
    return 0.75 * gamma * 0.5 * (1. + df.sign(func)) * func


def start_hook(newfolder, **namespace):
    statsfile = os.path.join(newfolder, "Statistics/stats.dat")
    return dict(statsfile=statsfile)
