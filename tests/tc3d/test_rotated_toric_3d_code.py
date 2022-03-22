import os
import json
from typing import Tuple, List
from abc import ABCMeta, abstractmethod
import pytest
import numpy as np
from tqdm import tqdm
from itertools import combinations
from qecsim.model import StabilizerCode
from bn3d.models import RotatedToric3DCode, RotatedToric3DPauli
from bn3d.bpauli import (
    bcommute, bvector_to_pauli_string, brank, apply_deformation
)
from bn3d.error_models import DeformedXZZXErrorModel
from bn3d.decoders import BeliefPropagationOSDDecoder
from scipy.special import comb

from .indexed_code_test import IndexedCodeTest


def operator_spec(code, bsf):
    """Get representation of BSF as list of (pauli, (x, y, z)) entries.

    Useful for debugging and reading BSF in human-readable format.
    """
    operator_spec = []
    pauli_string = bvector_to_pauli_string(bsf)
    for index, pauli in enumerate(pauli_string):
        if pauli != 'I':
            matches = [
                xyz
                for xyz, i in code.qubit_index.items()
                if i == index
            ]
            location = matches[0]
            operator_spec.append((pauli, location))
    return operator_spec


def print_non_commuting(
    code: StabilizerCode, commutators: np.ndarray,
    operators_1: np.ndarray, operators_2: np.ndarray,
    name_1: str, name_2: str,
    max_print: int = 5
):
    non_commuting = set([
        (i, j)
        for i, j in np.array(np.where(commutators)).T
        if i <= j
    ])

    # Print the first few non-commuting stabilizers if any found.
    if non_commuting:
        print(
            f'{len(non_commuting)} pairs of non-commuting '
            f'{name_1} and {name_2}:'
        )
        for i_print, (i, j) in enumerate(non_commuting):
            print(f'{name_1} {i} and {name_2} {j} anticommuting')
            operator_spec_1 = operator_spec(code, operators_1[i])
            operator_spec_2 = operator_spec(code, operators_2[j])
            overlap = [
                (op_1, op_2, site_1)
                for op_1, site_1 in operator_spec_1
                for op_2, site_2 in operator_spec_2
                if site_1 == site_2
            ]
            print('Overlap:', overlap)
            print(f'{name_1} {i}:', operator_spec_1)
            print(f'{name_2} {j}:', operator_spec_2)
            if i_print == max_print:
                n_remaining = len(non_commuting) - 1 - i_print
                if n_remaining > 0:
                    print(f'... {n_remaining} more pairs')
                break


@pytest.mark.skip(reason='sparse')
class IndexedCodeTestWithCoordinates(IndexedCodeTest, metaclass=ABCMeta):

    @property
    @abstractmethod
    def size(self) -> Tuple[int, int, int]:
        """Plane size x"""

    @property
    @abstractmethod
    def expected_plane_edges_xy(self) -> List[Tuple[int, int]]:
        """Expected xy coordinates of xy plane edges in unrotated lattice."""

    @property
    @abstractmethod
    def expected_plane_faces_xy(self) -> List[Tuple[int, int]]:
        """Expected xy coordinates of xy plane faces in unrotated lattice."""

    @property
    @abstractmethod
    def expected_plane_vertices_xy(self) -> List[Tuple[int, int]]:
        """Expected xy coordinates of xy plane vertices in unrot lattice."""

    @property
    @abstractmethod
    def expected_plane_z(self) -> List[int]:
        """Expected z coordinates of horizontal planes."""

    @property
    @abstractmethod
    def expected_vertical_z(self) -> List[int]:
        """Expected z coordinates of vertical edges and faces."""

    @pytest.fixture
    def code(self):
        return RotatedToric3DCode(*self.size)

    def test_qubit_indices(self, code):
        locations = set(code.qubit_index.keys())
        expected_locations = set([
            (x, y, z)
            for x, y in self.expected_plane_edges_xy
            for z in self.expected_plane_z
        ] + [
            (x, y, z)
            for x, y in self.expected_plane_vertices_xy
            for z in self.expected_vertical_z
        ])
        assert locations == expected_locations

    def test_vertex_indices(self, code):
        locations = set(code.vertex_index.keys())
        expected_locations = set([
            (x, y, z)
            for x, y in self.expected_plane_vertices_xy
            for z in self.expected_plane_z
        ])
        assert locations == expected_locations

    def test_face_indices(self, code):
        locations = set(code.face_index.keys())
        expected_locations = set([
            (x, y, z)
            for x, y in self.expected_plane_edges_xy
            for z in self.expected_vertical_z
        ] + [
            (x, y, z)
            for x, y in self.expected_plane_faces_xy
            for z in self.expected_plane_z
        ])
        assert locations == expected_locations

    def test_all_stabilizers_commute(self, code):
        commutators = bcommute(code.stabilizers, code.stabilizers)
        print_non_commuting(
            code, commutators, code.stabilizers, code.stabilizers,
            'stabilizer', 'stabilizer'
        )

        # There should be no non-commuting pairs of stabilizers.
        assert np.all(commutators == 0)

    def test_n_indepdent_stabilizers_equals_n_minus_k(self, code):
        n, k, _ = code.n_k_d
        matrix = code.stabilizers

        # Number of independent stabilizer generators.
        rank = brank(matrix)

        assert rank <= matrix.shape[0]
        assert rank == n - k

    def test_number_of_logicals_is_k(self, code):
        k = code.n_k_d[1]
        assert code.logical_xs.shape[0] == k
        assert code.logical_zs.shape[0] == k

    def test_logical_operators_anticommute_pairwise(self, code):
        k = code.n_k_d[1]
        assert np.all(bcommute(code.logical_xs, code.logical_xs) == 0)
        assert np.all(bcommute(code.logical_zs, code.logical_zs) == 0)
        commutators = bcommute(code.logical_xs, code.logical_zs)
        assert np.all(commutators == np.eye(k)), (
            f'Not pairwise anticommuting {commutators}'
        )

    def test_logical_operators_commute_with_stabilizers(self, code):
        x_commutators = bcommute(code.logical_xs, code.stabilizers)
        print_non_commuting(
            code, x_commutators, code.logical_xs, code.stabilizers,
            'logicalX', 'stabilizer'
        )
        z_commutators = bcommute(code.logical_zs, code.stabilizers)
        print_non_commuting(
            code, z_commutators, code.logical_zs, code.stabilizers,
            'logicalZ', 'stabilizer'
        )
        assert np.all(x_commutators == 0), (
            'logicalX not commuting with stabilizers'
        )
        assert np.all(z_commutators == 0), (
            'logicalZ not commuting with stabilizers'
        )

    def test_logical_operators_are_independent_by_rank(self, code):
        n, k, _ = code.n_k_d
        matrix = code.stabilizers

        # Number of independent stabilizer generators.
        rank = brank(matrix)

        assert rank == n - k

        matrix_with_logicals = np.concatenate([
            matrix,
            code.logical_xs,
            code.logical_zs,
        ])

        rank_with_logicals = brank(matrix_with_logicals)
        assert rank_with_logicals == n + k

    @pytest.mark.skip(reason='brute force')
    def test_find_lowest_weight_Z_only_logical_by_brute_force(self, code):
        n, k, _ = code.n_k_d
        matrix = code.stabilizers

        coords = {v: k for k, v in code.qubit_index.items()}

        min_weight = 4
        max_weight = 4
        for weight in range(min_weight, max_weight + 1):
            n_comb = comb(n, weight, exact=True)
            for sites in tqdm(combinations(range(n), weight), total=n_comb):
                logical = np.zeros(2*n, dtype=np.uint)
                for site in sites:
                    x, y, z = coords[site]
                    deform = False
                    if z % 2 == 1 and (x + y) % 4 == 2:
                        deform = True

                    if deform:
                        logical[site] = 1  # X operator on deformed
                    else:
                        logical[n + site] = 1  # Z operator on undeformed

                codespace = np.all(bcommute(matrix, logical) == 0)
                if codespace:
                    matrix_with_logical = np.concatenate([
                        matrix,
                        [logical]
                    ])
                    if brank(matrix_with_logical) == n - k + 1:
                        print('Found Z-only logical')
                        print([coords[site] for site in sites])
                        print(operator_spec(code, logical))
                        return


@pytest.mark.skip(reason='sparse')
class TestRotatedToric3DCode2x2x1(IndexedCodeTestWithCoordinates):
    size = (2, 2, 1)
    expected_plane_edges_xy = [
        (1, 1), (3, 1),
        (1, 3), (3, 3),
    ]
    expected_plane_faces_xy = [
        (2, 2), (4, 4)
    ]
    expected_plane_vertices_xy = [
        (4, 2), (2, 4)
    ]
    expected_plane_z = [1, 3]
    expected_vertical_z = [2]


@pytest.mark.skip(reason='sparse')
class TestRotatedToric3DCode3x2x1(IndexedCodeTestWithCoordinates):
    size = (3, 2, 1)
    expected_plane_edges_xy = [
        (1, 1), (3, 1), (5, 1),
        (1, 3), (3, 3), (5, 3),
    ]
    expected_plane_faces_xy = [
        (2, 2), (4, 4), (6, 2),
    ]
    expected_plane_vertices_xy = [
        (4, 2), (2, 4), (6, 4)
    ]
    expected_plane_z = [1, 3]
    expected_vertical_z = [2]


@pytest.mark.skip(reason='odd by odd')
class TestRotatedToric3DCode3x3x3(IndexedCodeTestWithCoordinates):
    size = (3, 3, 3)
    expected_plane_edges_xy = [
        (1, 1), (3, 1), (5, 1),
        (1, 3), (3, 3), (5, 3),
        (1, 5), (3, 5), (5, 5),
    ]
    expected_plane_faces_xy = [
        (2, 2), (6, 2),
        (4, 4),
        (2, 6), (6, 6),
    ]
    expected_plane_vertices_xy = [
        (4, 2),
        (2, 4), (6, 4),
        (4, 6),
    ]
    expected_plane_z = [1, 3, 5, 7]
    expected_vertical_z = [2, 4, 6]


@pytest.mark.skip(reason='sparse')
class TestRotatedToric3DCode4x3x3(IndexedCodeTestWithCoordinates):
    size = (4, 3, 3)
    expected_plane_edges_xy = [
        (1, 1), (3, 1), (5, 1), (7, 1),
        (1, 3), (3, 3), (5, 3), (7, 3),
        (1, 5), (3, 5), (5, 5), (7, 5),
    ]
    expected_plane_faces_xy = [
        (2, 2), (6, 2),
        (4, 4), (8, 4),
        (2, 6), (6, 6),
    ]
    expected_plane_vertices_xy = [
        (4, 2), (8, 2),
        (2, 4), (6, 4),
        (4, 6), (8, 6),
    ]
    expected_plane_z = [1, 3, 5, 7]
    expected_vertical_z = [2, 4, 6]


@pytest.mark.skip(reason='sparse')
class TestRotatedToric3DCode3x4x3(IndexedCodeTestWithCoordinates):
    size = (3, 4, 3)
    expected_plane_edges_xy = [
        (1, 1), (1, 3), (1, 5), (1, 7),
        (3, 1), (3, 3), (3, 5), (3, 7),
        (5, 1), (5, 3), (5, 5), (5, 7),
    ]
    expected_plane_faces_xy = [
        (2, 2), (2, 6),
        (4, 4), (4, 8),
        (6, 2), (6, 6),
    ]
    expected_plane_vertices_xy = [
        (2, 4), (2, 8),
        (4, 2), (4, 6),
        (6, 4), (6, 8),
    ]
    expected_plane_z = [1, 3, 5, 7]
    expected_vertical_z = [2, 4, 6]


@pytest.mark.skip(reason='sparse')
class TestRotatedToric3DCode4x4x3(IndexedCodeTestWithCoordinates):
    size = (4, 4, 3)
    expected_plane_edges_xy = [
        (1, 1), (1, 3), (1, 5), (1, 7),
        (3, 1), (3, 3), (3, 5), (3, 7),
        (5, 1), (5, 3), (5, 5), (5, 7),
        (7, 1), (7, 3), (7, 5), (7, 7),
    ]
    expected_plane_faces_xy = [
        (2, 2), (2, 6),
        (4, 4), (4, 8),
        (6, 2), (6, 6),
        (8, 4), (8, 8),
    ]
    expected_plane_vertices_xy = [
        (2, 4), (2, 8),
        (4, 2), (4, 6),
        (6, 4), (6, 8),
        (8, 2), (8, 6),
    ]
    expected_plane_z = [1, 3, 5, 7]
    expected_vertical_z = [2, 4, 6]


@pytest.mark.skip(reason='sparse')
class TestRotatedToric3DPauli:

    size = (4, 3, 3)

    @pytest.fixture
    def code(self):
        """Example code with co-prime x and y dimensions."""
        return RotatedToric3DCode(*self.size)

    def test_vertex_operator_in_bulk_has_weight_6(self, code):
        vertices = [
            (x, y, z)
            for x, y, z in code.vertex_index
            if z in [3, 5]
        ]
        for vertex in vertices:
            operator = RotatedToric3DPauli(code)
            operator.vertex('Z', vertex)
            assert sum(operator.to_bsf()) == 6

    def test_vertex_operator_on_boundary_has_weight_5(self, code):
        vertices = [
            (x, y, z)
            for x, y, z in code.vertex_index
            if z in [1, 7]
        ]
        for vertex in vertices:
            operator = RotatedToric3DPauli(code)
            operator.vertex('Z', vertex)
            assert sum(operator.to_bsf()) == 5

    def test_every_face_operator_in_bulk_has_weight_4(self, code):
        for face in code.face_index:
            x, y, z = face
            if x > 1 and y > 1 and z > 1 and z < 2*self.size[2] + 1:
                operator = RotatedToric3DPauli(code)
                operator.face('X', face)
                assert sum(operator.to_bsf()) == 4


@pytest.mark.skip(reason='sparse')
class TestRotatedToric3DDeformation:

    def test_deformation_index(self):
        code = RotatedToric3DCode(3, 4, 3)
        error_model = DeformedXZZXErrorModel(0.2, 0.3, 0.5)
        deformation_index = error_model._get_deformation_indices(code)
        coords_map = {
            index: coord for coord, index in code.qubit_index.items()
        }
        coords = [coords_map[index] for index in range(len(coords_map))]
        deformation_sites = sorted(
            [
                coords[i]
                for i, active in enumerate(deformation_index)
                if active
            ],
            key=lambda x: x[::-1]
        )
        assert all(z % 2 == 1 for x, y, z in deformation_sites)
        expected_sites_plane = [
            (1, 1), (5, 1), (3, 3), (1, 5), (5, 5), (3, 7)
        ]
        expected_sites = []
        for z in [1, 3, 5, 7]:
            expected_sites += [(x, y, z) for x, y in expected_sites_plane]
        expected_sites.sort(key=lambda x: x[::-1])
        assert deformation_sites == expected_sites

    @pytest.mark.skip(reason='sparse')
    def test_stabilizer_matrix(self):
        project_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(__file__))
        )
        entries = []
        for size in [3]:
            L_x, L_y, L_z = size, size + 1, size
            print(L_x, L_y, L_z)
            code = RotatedToric3DCode(L_x, L_y, L_z)
            out_json = os.path.join(project_dir, 'temp', 'rotated_toric_3d_test.json')
            error_model = DeformedXZZXErrorModel(0.2, 0.3, 0.5)
            deformation_index = error_model._get_deformation_indices(code)
            coords_map = {
                index: coord for coord, index in code.qubit_index.items()
            }
            coords = [coords_map[index] for index in range(len(coords_map))]
            entries.append({
                'size': [L_x, L_y, L_z],
                'coords': coords,
                'undeformed': {
                    'n_k_d': code.n_k_d,
                    'stabilizers': code.stabilizers.tolist(),
                    'logical_xs': code.logical_xs.tolist(),
                    'logical_zs': code.logical_zs.tolist(),
                },
                'deformed': {
                    'n_k_d': code.n_k_d,
                    'stabilizers': apply_deformation(
                        deformation_index, code.stabilizers
                    ).tolist(),
                    'logical_xs': apply_deformation(
                        deformation_index, code.logical_xs
                    ).tolist(),
                    'logical_zs': apply_deformation(
                        deformation_index, code.logical_zs
                    ).tolist(),
                }
            })
        with open(out_json, 'w') as f:
            json.dump({
                'entries': entries
            }, f)


@pytest.mark.skip(reason='sparse')
class TestBPOSDOnRotatedToric3DCodeOddTimesEven:

    @pytest.mark.parametrize('pauli', ['X', 'Y', 'Z'])
    def test_decode_single_qubit_error(self, pauli):
        code = RotatedToric3DCode(3, 4, 3)
        error_model = DeformedXZZXErrorModel(1/3, 1/3, 1/3)
        probability = 0.1
        decoder = BeliefPropagationOSDDecoder(error_model, probability)

        failing_cases: List[Tuple[int, int, int]] = []
        for site in code.qubit_index:
            error_pauli = RotatedToric3DPauli(code)
            error_pauli.site(pauli, site)
            error = error_pauli.to_bsf()
            syndrome = bcommute(code.stabilizers, error)
            correction = decoder.decode(code, syndrome)
            total_error = (error + correction) % 2
            if not np.all(bcommute(code.stabilizers, total_error) == 0):
                failing_cases.append(site)
        n_failing = len(failing_cases)
        max_show = 100
        if n_failing > max_show:
            failing_cases_show = ', '.join(map(str, failing_cases[:max_show]))
            end_part = f'...{n_failing - max_show} more'
        else:
            failing_cases_show = ', '.join(map(str, failing_cases))
            end_part = ''

        assert n_failing == 0, (
            f'Failed decoding {n_failing} {pauli} errors at '
            f'{failing_cases_show} {end_part}'
        )