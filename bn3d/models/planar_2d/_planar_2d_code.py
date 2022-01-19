from typing import Tuple
import numpy as np
from ..generic._indexed_sparse_code import IndexedSparseCode
from ._planar_2d_pauli import Planar2DPauli
from ... import bsparse


class Planar2DCode(IndexedSparseCode):

    pauli_class = Planar2DPauli

    # StabilizerCode interface methods.

    @property
    def n_k_d(self) -> Tuple[int, int, int]:
        Lx, Ly = self.size
        return (Lx*Ly + (Lx-1)*(Ly-1), 1, min(self.size))

    @property
    def dimension(self) -> int:
        return 2

    @property
    def label(self) -> str:
        return 'Toric {}x{}'.format(*self.size)

    @property
    def logical_xs(self) -> np.ndarray:
        """The 2 logical X operators."""

        if self._logical_xs.size == 0:
            Lx, Ly = self.size
            logicals = bsparse.empty_row(2*self.n_k_d[0])

            # X operators along x edges in x direction.
            logical = self.pauli_class(self)
            for x in range(1, 2*Lx, 2):
                logical.site('X', (x, 0))
            logicals = bsparse.vstack([logicals, logical.to_bsf()])

            self._logical_xs = logicals

        return self._logical_xs

    @property
    def logical_zs(self) -> np.ndarray:
        """Get the 3 logical Z operators."""
        if self._logical_zs.size == 0:
            Lx, Ly = self.size
            logicals = bsparse.empty_row(2*self.n_k_d[0])

            # Z operators on x edges forming surface normal to x (yz plane).
            logical = self.pauli_class(self)
            for y in range(0, 2*Ly, 2):
                logical.site('Z', (1, y))
            logicals = bsparse.vstack([logicals, logical.to_bsf()])

            self._logical_zs = logicals

        return self._logical_zs

    def axis(self, location):
        x, y = location

        if (x % 2 == 1) and (y % 2 == 0):
            axis = self.X_AXIS
        elif (x % 2 == 0) and (y % 2 == 1):
            axis = self.Y_AXIS
        else:
            raise ValueError(f'Location {location} does not correspond to a qubit')

        return axis

    def _create_qubit_indices(self):
        coordinates = []
        Lx, Ly = self.size

        # Qubits along e_x
        for x in range(1, 2*Lx, 2):
            for y in range(0, 2*Ly, 2):
                coordinates.append((x, y))

        # Qubits along e_y
        for x in range(2, 2*Lx, 2):
            for y in range(1, 2*Ly-1, 2):
                coordinates.append((x, y))

        coord_to_index = {coord: i for i, coord in enumerate(coordinates)}

        return coord_to_index

    def _create_vertex_indices(self):
        coordinates = []
        Lx, Ly = self.size

        for x in range(2, 2*Lx, 2):
            for y in range(0, 2*Ly, 2):
                coordinates.append((x, y))

        coord_to_index = {coord: i for i, coord in enumerate(coordinates)}

        return coord_to_index

    def _create_face_indices(self):
        coordinates = []
        Lx, Ly = self.size

        for x in range(1, 2*Lx, 2):
            for y in range(1, 2*Ly-1, 2):
                coordinates.append((x, y))

        coord_to_index = {coord: i for i, coord in enumerate(coordinates)}

        return coord_to_index