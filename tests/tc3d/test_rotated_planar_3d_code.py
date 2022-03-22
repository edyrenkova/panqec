import pytest
import numpy as np
from bn3d.models import RotatedPlanarCode3D
from .indexed_code_test import IndexedCodeTest


# TODO fix n_k_d for unequal sizes
@pytest.mark.parametrize('L_x, L_y, L_z', [
    (2, 2, 2),
    (3, 3, 3),
    (4, 4, 4),
    pytest.param(3, 2, 2, marks=pytest.mark.xfail),
    pytest.param(2, 3, 2, marks=pytest.mark.xfail),
    (2, 2, 3),
])
def test_n_qubits_equals_len_qubit_index(L_x, L_y, L_z):
    code = RotatedPlanarCode3D(L_x, L_y, L_z)
    assert code.n_k_d[0] == len(code.qubit_index)


class TestRotatedPlanarCode3D(IndexedCodeTest):

    L_x = 4
    L_y = 4
    L_z = 4

    @pytest.fixture
    def code(self):
        """Example code with non-uniform dimensions."""
        new_code = RotatedPlanarCode3D(self.L_x, self.L_y, self.L_z)
        return new_code

    def test_vertex_index_corner_region(self, code):

        # First layer corner near origin
        assert (0, 0, 0) not in code.vertex_index
        assert (2, 0, 1) in code.vertex_index
        assert (2, 4, 1) in code.vertex_index
        assert (0, 2, 1) not in code.vertex_index
        assert (4, 2, 1) in code.vertex_index
        assert (6, 0, 1) in code.vertex_index
        assert (2, 8, 1) in code.vertex_index
        assert (4, 6, 1) in code.vertex_index

    def test_vertex_index_complies_with_rules(self, code):
        for x, y, z in code.vertex_index.keys():
            assert z % 2 == 1
            if x % 4 == 2:
                assert y % 4 == 0
            else:
                assert x % 4 == 0
                assert y % 4 == 2

    def test_vertex_index_boundary_conditions(self, code):
        for x, y, z in code.vertex_index.keys():
            assert x != 0
            if y == 0:
                assert x % 4 == 2
            if self.L_x % 2 == 0:
                if x == self.L_x*2 + 2:
                    assert y % 4 == 0

    def test_face_index_complies_with_rules(self, code):
        for x, y, z in code.face_index.keys():
            if z % 2 == 1:
                if x % 4 == 0:
                    assert y % 4 == 0
                else:
                    assert x % 4 == 2
                    assert y % 4 == 2
            else:
                assert x % 2 == 1
                assert y % 2 == 1

    def test_qubit_index_complies_with_rules(self, code):
        for x, y, z in code.qubit_index.keys():
            if z % 2 == 1:
                assert x % 2 == 1
                assert y % 2 == 1
            else:
                if x % 4 == 0:
                    assert y % 4 == 2
                else:
                    assert x % 4 == 2
                    assert y % 4 == 0

    def test_each_qubit_contained_in_1_or_2_check_operators(self, code):
        H = code.Hx
        assert np.all(H.sum(axis=0) > 0)
        assert np.all(H.sum(axis=0) <= 2)