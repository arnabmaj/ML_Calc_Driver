r"""
The :class:`Phonon` class allows to compute the normal modes
and vibration energies of a system using a machine
learning trained model.
"""

import numpy as np
from mlcalcdriver import Job, Posinp
from mlcalcdriver.calculators import Calculator
from mlcalcdriver.workflows import Geopt
from copy import deepcopy
from mlcalcdriver.globals import ANG_TO_B, B_TO_ANG, EV_TO_HA, HA_TO_CMM1, AMU_TO_EMU


class Phonon:
    r"""
    This class allows to run all the calculations enabling the
    computation of the phonon energies of a given system, using
    machine learning models.

    To get the phonon energies of the system, one needs to find the
    eigenvalues of the dynamical matrix, that is closely related to the
    Hessian matrix. To build these matrices, one must find the
    derivatives of the forces when each coordinate of each atom is
    translated by a small amount around the equilibrium positions.
    """

    def __init__(self, posinp, calculator, relax=True, translation_amplitudes=None):
        r"""
        The initial position fo the atoms are taken from the `init_state`
        Posinp instance. If they are not part of a relaxed geometry, the
        relax parameter should stay at `True`.

        WARNING: Relaxed geometries are dependent on the model chosen to
        define the calculator. In doubt, `relax` parameter should be ignored.

        The distance of the displacement in each direction is controlled
        by `translation_amplitudes`.

        Phonon energies and normal modes are calculated using the `run()`method.
        This method creates the additional structures needed, passes them to a 
        `Job` instance, then post-processes the obtained forces
        to obtain them.

        Parameters
        ----------
        posinp : mlcaldriver.Posinp
            Initial positions of the system under consideration.
        calculator : Calculator
            mlcalcdriver.Calculator instance that will be used in
            the created Jobs to evaluate properties.
        relax : bool
            Wether the initial positions need to be relaxed or not.
            Default is `True`.
        translation_amplitudes: list of length 3
            Amplitudes of the translations to be applied to each atom
            along each of the three space coordinates (in angstroms).
        order : int
            Order of the numerical differentiation used to compute the
            dynamical matrix. Can be either 1, 2 or 3.
        """
        self.posinp = posinp
        self.calculator = calculator
        self.relax = relax
        self.translation_amplitudes = translation_amplitudes

        if self.relax:
            self._ground_state = None
        else:
            self._ground_state = deepcopy(self.posinp)

        self.dyn_mat = None
        self.energies = None
        self.normal_modes = None

    @property
    def posinp(self):
        r"""
        Returns
        -------
        posinp : Posinp
            Initial positions of the system for which phonon properties
            will be calculated.
        """
        return self._posinp

    @posinp.setter
    def posinp(self, posinp):
        if isinstance(posinp, Posinp):
            self._posinp = posinp
        else:
            raise TypeError(
                "Initial positions should be given in a mlcalcdriver.Posinp instance."
            )

    @property
    def calculator(self):
        r"""
        Returns
        -------
        Calculator
            The Calculator object to use for the Jobs necessary to
            perform the phonons calculations.	
        """
        return self._calculator

    @calculator.setter
    def calculator(self, calculator):
        if isinstance(calculator, Calculator):
            self._calculator = calculator
        else:
            raise TypeError(
                """
                The calculator for the Phonon instance must be a class or a
                metaclass derived from mlcalcdriver.calculators.Calculator.
                """
            )

    @property
    def translation_amplitudes(self):
        r"""
        Returns
        -------
        translation_amplitudes : float
            Displacements of atoms in all three dimensions to calculate
            the phonon properties. Default is 0.03 Angstroms.
        """
        return self._translation_amplitudes

    @translation_amplitudes.setter
    def translation_amplitudes(self, translation_amplitudes):
        if translation_amplitudes == None:
            self._translation_amplitudes = 0.03
        else:
            self._translation_amplitudes = float(translation_amplitudes)

    @property
    def relax(self):
        r"""
        Returns
        -------
        relax : bool
            If `True`, which is default, the initial positions are relaxed
            before the phonon properties are calculated. Recommended,
            especially if more than one model is used.
        """
        return self._relax

    @relax.setter
    def relax(self, relax):
        relax = bool(relax)
        self._relax = relax

    @property
    def energies(self):
        r"""
        Returns
        -------
        numpy.array or None
            Phonon energies of the system (units: cm^-1).
        """
        return self._energies

    @energies.setter
    def energies(self, energies):
        self._energies = energies

    @property
    def dyn_mat(self):
        r"""
        Returns
        -------
        numpy.array or None
            Dynamical matrix deduced from the calculations.
        """
        return self._dyn_mat

    @dyn_mat.setter
    def dyn_mat(self, dyn_mat):
        self._dyn_mat = dyn_mat

    @property
    def normal_modes(self):
        r"""
        Returns
        -------
        numpy.array or None
            Normal modes of the system found as eigenvectors of the
            dynamical matrix.
        """
        return self._normal_modes

    @normal_modes.setter
    def normal_modes(self, normal_modes):
        self._normal_modes = normal_modes

    def run(self, device="cpu", batch_size=128, **kwargs):
        r"""
        Parameters
        ----------
        device : str
            Either "cpu" or "cuda" to run on cpu or gpu. Default is 'cpu'
            and should be faster in most cases.
        batch_size : int
            Batch size used when passing the structures to the model
        **kwargs : 
            Optional arguments for the geometry optimization.
            Only useful if the relaxation is unstable.
        """
        if self.relax:
            geopt = Geopt(posinp=self.posinp, calculator=self.calculator, **kwargs)
            geopt.run(
                device=device,
                batch_size=batch_size,
            )
            self._ground_state = deepcopy(geopt.final_posinp)
        
        job = Job(posinp=self._create_displacements(), calculator=self.calculator)
        job.run(
            property="forces",
            device=device,
            batch_size=batch_size,
        )
        self._post_proc(job)

    def _create_displacements(self):
        r"""
        Set the displacements each atom must undergo from the amplitudes
        of displacement in each direction. The numerical derivatives are obtained
        with the five-point stencil method.
        """
        structs = []
        for i in range(len(self._ground_state)):
            for dim in [np.array([1, 0, 0]), np.array([0, 1, 0]), np.array([0, 0, 1])]:
                for factor in [2, 1, -1, -2]:
                    structs.append(
                        self._ground_state.translate_atom(
                            i, self.translation_amplitudes * dim * factor
                        )
                    )
        return structs

    def _post_proc(self, job):
        r"""
        Calculates the energies and normal modes from the results
        obtained from the model.
        """
        self.dyn_mat = self._compute_dyn_mat(job)
        self.energies, self.normal_modes = self._solve_dyn_mat()
        self.energies[::-1].sort()
        self.energies *= HA_TO_CMM1

    def _compute_dyn_mat(self, job):
        r"""
        Computes the dynamical matrix
        """
        hessian = self._compute_hessian(job)
        masses = self._compute_masses()
        return hessian / masses

    def _compute_masses(self):
        r"""
        Creates the masses matrix
        """
        to_mesh = [atom.mass for atom in self._ground_state for _ in range(3)]
        m_i, m_j = np.meshgrid(to_mesh, to_mesh)
        return np.sqrt(m_i * m_j) * AMU_TO_EMU

    def _compute_hessian(self, job):
        r"""
        Computes the hessian matrix from the forces
        """
        n_at = len(self.posinp)
        hessian = np.zeros((3 * n_at, 3 * n_at))
        forces = np.array(job.results["forces"]) * EV_TO_HA * B_TO_ANG
        for i in range(3 * n_at):
            hessian[i, :] = (
                -forces[4 * i].flatten()
                + forces[4 * i + 3].flatten()
                + 8 * (forces[4 * i + 1].flatten() - forces[4 * i + 2].flatten())
            ) / (12 * self.translation_amplitudes * ANG_TO_B)
        return -(hessian + hessian.T) / 2.0

    def _solve_dyn_mat(self):
        r"""
        Obtains the eigenvalues and eigenvectors from
        the dynamical matrix
        """
        eigs, vecs = np.linalg.eig(self.dyn_mat)
        eigs = np.sign(eigs) * np.sqrt(np.where(eigs < 0, -eigs, eigs))
        return eigs, vecs
