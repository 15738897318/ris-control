import scenario.common as cmn
from scenario.cluster import Cluster

try:
    import cupy as np
except ImportError:
    import numpy as np

import argparse
from scipy.constants import speed_of_light

import matplotlib.pyplot as plt

from os import path

# GLOBAL STANDARD PARAMETERS
# The following is a wrapper that generates a directory where automatically save plots
OUTPUT_DIR = cmn.standard_output_dir('ris-protocol')
DATADIR = path.join(path.dirname(__file__), 'data')

# Set parameters
NUM_EL_X = 10
CARRIER_FREQ = 3e9            # [Hz]
BANDWIDTH = 180e3               # [Hz]
NOISE_POWER_dBm = -94               # [dBm]
SIDE = 20                       # [m] side of the room
BS_POS = np.array([[20, 5, 5]]) # Standard BS positioning

NUM_PILOTS = 1          # number of pilots in a TTI
T = 7 * 1/14            # [ms] time of a TTI
N_TTIs = 20             # minimum coherence block (10 ms)
TX_POW_dBm = 24         # [dBm] transmit power
try:
    TAU = T * np.arange(N_TTIs, 20 * N_TTIs + 1, 10).get()
except AttributeError:
    TAU =  T * np.arange(N_TTIs, 20 * N_TTIs + 1, 10)


# Parser for the test files
def command_parser():
    """Parse command line using arg-parse and get user data to run the render.
        If no argument is given, no data is saved  and the default values are used.
        
        :return: the parsed arguments
    """
    # Parse depending on the boolean watch flag
    parser = argparse.ArgumentParser()
    parser.add_argument("-r", "--render", action="store_true", default=False)
    parser.add_argument("-D", type=float, default=SIDE)
    parser.add_argument("-f", "--filename", default='')
    parser.add_argument("-d", "--directory", default=DATADIR)
    args: dict = vars(parser.parse_args())
    return list(args.values())


## Classes
class RisProtocolEnv(Cluster):
    """General environment class for the setting at hand"""
    def __init__(self,
                 num_users: int,
                 side: float = SIDE,
                 bs_position: np.array = BS_POS,
                 ris_num_els: int = NUM_EL_X,
                 carrier_frequency: float = CARRIER_FREQ,
                 bandwidth: float = BANDWIDTH,
                 noise_power: float = NOISE_POWER_dBm,
                 rbs: int = 1,
                 rng: np.random.RandomState = None):
        # Generate sides of the overall environment
        max_pos = max(side, np.max(bs_position))
        sides = 2 * np.array([max_pos, max_pos, max_pos])
        # Init parent class
        super().__init__(shape='box',
                         sizes=np.array(sides),
                         carrier_frequency=carrier_frequency,
                         bandwidth=bandwidth,
                         noise_power=noise_power,
                         direct_channel='LoS',
                         reflective_channel='LoS',
                         rbs=rbs,
                         rng=rng)
        # Manage cupy/numpy compatibilities
        try:
            bs_position = np.asarray(bs_position)
        except AttributeError:
            pass
        # Generate user position
        x = side * np.random.rand(num_users, 1) - side / 2
        y = side * np.random.rand(num_users, 1)
        z = - side * np.random.rand(num_users, 1)
        ue_position = np.hstack((x, y, z))

        # Geometry and scenario
        # Place the BS in the selected position
        self.place_bs(1, bs_position)
        # Place the UE in the selected positions
        self.place_ue(ue_position.shape[0], ue_position)
        # Place the RIS with some standard values
        self.place_ris(1, np.array([[0, 0, 0]]), num_els_x=ris_num_els, dist_els_x=self.wavelength/2, orientation='xz')
        self.compute_distances()
        # Initialize standard configuration at -3dB
        self.ris.init_std_configurations(self.wavelength, )


    def set_std_conf_2D(self, index):
        return self.ris.set_std_configuration_2D(self.wavelength, index, bs_pos=self.bs.pos)


    def load_conf(self, azimuth_angle: float, elevation_angle: float) -> tuple:
        """Load the configuration pointing towards azimuth and elevation given as input when the for the current setting
        (i.e. when the RIS is oriented in the x-y plane).
        
        ---- Inputs:
        :param azimuth_angle: float, azimuth angle \varphi in rad
        :param elevation_angle: float, elevation angle \theta in rad
        ---- Output
        :return: tuple, containing the point on the floor where the RIS is pointing to and
                       the loaded configuration as a vector
        """
        self.x_hat = self.pointing(float(azimuth_angle), float(elevation_angle))
        return self.x_hat, self.ris.load_conf(self.wavenumber, np.array(azimuth_angle), np.array(elevation_angle), self.bs.pos)

    def pointing(self, azimuth_angle: float, elevation_angle: float, k_max = 1):
        """Return the point on the floor corresponding to the input azimyth and elevation.

        ---- Inputs:
        :param azimuth_angle: float, azimuth angle \varphi in rad
        :param elevation_angle: float, elevation angle \theta in rad
        :param k_max: int, DEPRECATED used for testing the grating lobes
        ---- Output
        :return: np.ndarray (1,3), corresponding point on the floor of the scenario
        """
        k = np.arange(0, k_max)
        x_pointing = k * 2 * self.wavelength / np.sqrt(self.ris.num_els_h) / self.ris.dist_els_h + np.cos(azimuth_angle) * np.sin(elevation_angle)
        y_pointing = k * 2 * self.wavelength / np.sqrt(self.ris.num_els_h) / self.ris.dist_els_h + np.sin(azimuth_angle) * np.sin(elevation_angle)
        z_pointing = np.sqrt(1 - x_pointing ** 2 - y_pointing ** 2)
        return self.z_size / z_pointing[:, np.newaxis] * np.array([x_pointing, y_pointing, z_pointing]).T


    def pos2beta(self, x):
        """Compute the value of the pathloss (linear scale) given position in space

        ---- Inputs:
        :param x: np.ndarray (K, 3), K position to compute \beta for
        ---- Output
        :return  np.ndarray (K,), value of the path loss gain (linear scale)
        """
        pl = 10 * self.pl_exponent * np.log10(self.dist_br * np.linalg.norm(np.array(x), axis=-1))
        pl += -(self.bs.gain + self.ue.gain)
        pl += - 40 * np.log10(self.wavelength / 4 / np.pi / self.ref_dist)
        pl += - 20 * self.pl_exponent * np.log10(self.ref_dist)
        return 10 ** (-pl / 10)


    def compute_afgain(self, x):
        """ Utils function to compute AF gain given a position in space.

        ---- Input:
        :param x: np.ndarray (K, 3), position of the K points to estimate the AF gain for
        ---- Output:
        :return: np.ndarray (K,), computed AF gain
        """
        # Preprocessing
        N = x.shape[0]
        pos_dist = np.linalg.norm(x, axis=-1)
        pos_versor = x / pos_dist[np.newaxis].T
        # Compute the array on a subset of points for RAM reason
        af_gain = np.zeros(N)
        # max test per iteration
        n = int(1e6)
        # iterations
        iter = int(np.floor(N / n))
        # Phase bs ris is always the same
        phase_shift_br = self.freqs[np.newaxis].T * np.tile((self.dist_br - self.bs.pos.cartver @ self.ris.el_pos)[np.newaxis].T, (1, self.RBs, n))

        # Iterating to smaller set of data to avoid RAM or GPU memory limits
        for i in np.arange(iter):
            phase_shift_ru = self.freqs[np.newaxis].T * (pos_dist[i*n:(i+1)*n] - (pos_versor[i*n:(i+1)*n] @ self.ris.el_pos).T)[np.newaxis].reshape((self.ris.num_els, 1, n))
            af_gain[i*n:(i+1)*n] = np.abs(np.sum(self.ris.actual_conf[np.newaxis, np.newaxis].T * np.exp(- 1j * 2 * np.pi / speed_of_light * (phase_shift_ru + phase_shift_br)), axis=0) / self.ris.num_els) ** 2
        # deal with non integer division N / n
        n2 = N - iter * n
        if n2 > 0:
            phase_shift_ru = self.freqs[np.newaxis].T * (pos_dist[iter * n:] - (pos_versor[iter * n:] @ self.ris.el_pos).T)[np.newaxis].reshape((self.ris.num_els, 1, n2))
            phase_shift_br = self.freqs[np.newaxis].T * np.tile((self.dist_br - self.bs.pos.cartver @ self.ris.el_pos)[np.newaxis].T, (1, self.RBs, n2))
            af_gain[iter * n:] = np.abs(np.sum(self.ris.actual_conf[np.newaxis, np.newaxis].T * np.exp(- 1j * 2 * np.pi / speed_of_light * (phase_shift_ru + phase_shift_br)), axis=0) / self.ris.num_els) ** 2
        del phase_shift_ru, phase_shift_br, pos_versor, pos_dist
        return af_gain

    def plot_scenario(self, render: bool = False, *args):
        # Plot setup
        fig = plt.figure()
        ax = fig.add_subplot(projection='3d')

        try:
            ax.scatter(self.ue.pos.cart[:, 0], self.ue.pos.cart[:, 1], self.ue.pos.cart[:, 2], marker='o', color='black', alpha=0.1, label='UE')
            ax.scatter(self.bs.pos.cart[:, 0], self.bs.pos.cart[:, 1], self.bs.pos.cart[:, 2], marker='^', label='BS')
            ax.scatter(self.ris.pos.cart[:, 0], self.ris.pos.cart[:, 1], self.ris.pos.cart[:, 2], marker='d', label='RIS')
        except TypeError:
            ax.scatter(self.ue.pos.cart[:, 0].get(), self.ue.pos.cart[:, 1].get(), self.ue.pos.cart[:, 2].get(), marker='o', color='black', alpha=0.1, label='UE')
            ax.scatter(self.bs.pos.cart[:, 0].get(), self.bs.pos.cart[:, 1].get(), self.bs.pos.cart[:, 2].get(), marker='^', label='BS')
            ax.scatter(self.ris.pos.cart[:, 0].get(), self.ris.pos.cart[:, 1].get(), self.ris.pos.cart[:, 2].get(), marker='d', label='RIS')

        ax.set_xlabel('$x$')
        ax.set_ylabel('$y$')
        ax.set_zlabel('$z$')

        ax.legend()
        plt.show()



def ecdf(a):
    """Empirical CDF evaluation of a rv.

    ---- Input:
    :param a: np.ndarray (K,), realization of a rv
    ---- Outputs:
    :return: tuple, collecting the inverse eCDF and the eCDF of the rv
    """
    x, counts = np.unique(a, return_counts=True)
    cusum = np.cumsum(counts)
    try:
        return np.asnumpy(x), np.asnumpy(cusum / cusum[-1])
    except AttributeError:
        return x, cusum / cusum[-1]
