import itertools
import pytest
import numpy as np
from bn3d.models import ToricCode3D, Toric3DPauli
from bn3d.decoders import SweepDecoder3D
from bn3d.bpauli import bcommute, bsf_wt
from bn3d.noise import PauliErrorModel
from bn3d.utils import dict_where, set_where, edge_coords, face_coords


class TestSweepDecoder3D:

    @pytest.fixture
    def code(self):
        return ToricCode3D(3, 4, 5)

    @pytest.fixture
    def decoder(self):
        return SweepDecoder3D()

    def test_decoder_has_required_attributes(self, decoder):
        assert decoder.label is not None
        assert decoder.decode is not None

    def test_decode_trivial_syndrome(self, decoder, code):
        syndrome = np.zeros(shape=code.stabilizers.shape[0], dtype=np.uint)
        correction = decoder.decode(code, syndrome)
        assert correction.shape == (1, 2*code.n_k_d[0])
        assert np.all(bcommute(code.stabilizers, correction) == 0)
        assert issubclass(correction.dtype.type, np.integer)

    @pytest.mark.parametrize('site', [
        (2, 1, 2),
        (1, 2, 0),
        (2, 1, 0),
        (0, 0, 7),
    ])
    def test_decode_Z_error(self, decoder, code, site):
        error = Toric3DPauli(code)
        error.site('Z', site)
        assert bsf_wt(error.to_bsf()) == 1

        # Measure the syndrome and ensure non-triviality.
        syndrome = code.measure_syndrome(error)
        assert np.any(syndrome != 0)

        correction = decoder.decode(code, syndrome)
        total_error = (error.to_bsf().toarray() + correction.toarray()) % 2
        assert np.all(bcommute(code.stabilizers.toarray(), total_error) == 0)

    def test_decode_many_Z_errors(self, decoder, code):
        error = Toric3DPauli(code)
        error.site('Z', (1, 2, 0))
        error.site('Z', (2, 1, 0))
        error.site('Z', (0, 0, 7))
        assert bsf_wt(error.to_bsf()) == 3

        syndrome = code.measure_syndrome(error)
        assert np.any(syndrome != 0)

        correction = decoder.decode(code, syndrome)
        total_error = (error.to_bsf().todense() + correction) % 2
        assert np.all(bcommute(code.stabilizers, total_error) == 0)

    def test_unable_to_decode_X_error(self, decoder, code):
        error = Toric3DPauli(code)
        error.site('X', (1, 0, 2))
        assert bsf_wt(error.to_bsf()) == 1

        syndrome = code.measure_syndrome(error)
        assert np.any(syndrome != 0)

        correction = decoder.decode(code, syndrome)
        assert np.all(correction.todense() == 0)

        total_error = (error.to_bsf().todense() + correction) % 2
        assert np.all(error.to_bsf() == total_error)

        assert np.any(bcommute(code.stabilizers, total_error) != 0)

    def test_decode_many_codes_and_errors_with_same_decoder(self, decoder):

        codes = [
            ToricCode3D(3, 4, 5),
            ToricCode3D(3, 3, 3),
            ToricCode3D(5, 4, 3),
        ]

        sites = [
            (0, 0, 1),
            (1, 0, 0),
            (0, 1, 0)
        ]

        for code, site in itertools.product(codes, sites):
            error = Toric3DPauli(code)
            error.site('Z', site)
            syndrome = code.measure_syndrome(error)
            correction = decoder.decode(code, syndrome)
            total_error = (error.to_bsf().todense() + correction) % 2
            assert np.all(bcommute(code.stabilizers, total_error) == 0)

    def test_decode_error_on_two_edges_sharing_same_vertex(self):
        code = ToricCode3D(3, 3, 3)
        decoder = SweepDecoder3D()
        error_pauli = Toric3DPauli(code)
        error_pauli.site('Z', (1, 0, 0))
        error_pauli.site('Z', (0, 1, 0))
        error = error_pauli.to_bsf()
        syndrome = bcommute(code.stabilizers, error)
        correction = decoder.decode(code, syndrome)
        total_error = (error.todense() + correction) % 2
        assert np.all(bcommute(code.stabilizers, total_error) == 0)

    def test_decode_with_general_Z_noise(self):
        code = ToricCode3D(3, 3, 3)
        decoder = SweepDecoder3D()
        np.random.seed(0)
        error_model = PauliErrorModel(0, 0, 1)

        in_codespace = []
        for i in range(100):
            error = error_model.generate(
                code, probability=0.1, rng=np.random
            )
            syndrome = bcommute(code.stabilizers, error)
            correction = decoder.decode(code, syndrome)
            total_error = (error + correction) % 2
            in_codespace.append(
                np.all(bcommute(code.stabilizers, total_error) == 0)
            )

        # Some will just be fails and not in code space, but assert that at
        # least some of them ended up in the code space.
        assert any(in_codespace)

    @pytest.mark.parametrize(
        'edge_location, faces_flipped',
        [
            ((1, 0, 0), {
                (1, 1, 0), (1, 5, 0), (1, 0, 1), (1, 0, 5)
            }),
            ((0, 1, 0), {
                (1, 1, 0), (5, 1, 0), (0, 1, 1), (0, 1, 5)
            }),
            ((0, 0, 1), {
                (1, 0, 1), (5, 0, 1), (0, 1, 1), (0, 5, 1)
            }),
        ]
    )
    def test_flip_edge(self, edge_location, faces_flipped):
        code = ToricCode3D(3, 3, 3)
        decoder = SweepDecoder3D()
        signs = decoder.get_initial_state(
            code, np.zeros(code.stabilizers.shape[0])
        )
        decoder.flip_edge(edge_location, signs, code)
        assert dict_where(signs) == faces_flipped

    def test_decode_loop_step_by_step(self):
        code = ToricCode3D(3, 3, 3)
        decoder = SweepDecoder3D()

        error_pauli = Toric3DPauli(code)
        sites = edge_coords([
            (0, 0, 0, 0), (1, 1, 0, 0), (0, 0, 1, 0), (1, 0, 0, 0),
        ], code.size)
        for site in sites:
            error_pauli.site('Z', site)
        assert find_sites(error_pauli) == set(sites)
        error = error_pauli.to_bsf()

        # Intialize the correction.
        correction = Toric3DPauli(code)

        # Compute the syndrome.
        syndrome = bcommute(code.stabilizers, error)

        signs = decoder.get_initial_state(code, syndrome)
        assert np.all(
            rebuild_syndrome(code, signs)[:code.n_k_d[0]]
            == syndrome[:code.n_k_d[0]]
        )
        assert dict_where(signs) == set(face_coords([
            (0, 0, 0, 0), (0, 0, 0, 2), (0, 1, 0, 0), (0, 1, 0, 2),
            (1, 0, 0, 0), (1, 0, 0, 2), (1, 0, 1, 0), (1, 0, 1, 2),
            (2, 0, 1, 0), (2, 0, 2, 0), (2, 1, 0, 0), (2, 2, 0, 0),
        ], code.size))

        signs = decoder.sweep_move(signs, correction, code)
        assert find_sites(correction) == set(edge_coords([
            (0, 0, 1, 0), (1, 1, 0, 0),
            (2, 0, 0, 0), (2, 0, 0, 2),
        ], code.size))
        assert dict_where(signs) == set(face_coords([
            (0, 0, 2, 0), (0, 0, 2, 2),
            (1, 2, 0, 0), (1, 2, 0, 2),
            (2, 2, 0, 0), (2, 0, 2, 0),
        ], code.size))

        signs = decoder.sweep_move(signs, correction, code)
        assert find_sites(correction) == set(edge_coords([
            (0, 0, 1, 0), (1, 1, 0, 0),
            (2, 0, 0, 0), (2, 0, 0, 2),
            (0, 2, 0, 0), (1, 0, 2, 0)
        ], code.size))
        assert np.all(np.array(list(signs.values())) == 0)

        total_error = (error + correction.to_bsf().todense()) % 2
        vertex_operator = Toric3DPauli(code)
        vertex_operator.vertex('Z', (0, 0, 0))
        assert np.all(total_error == vertex_operator.to_bsf())

        assert np.all(bcommute(code.stabilizers, total_error) == 0)

    def test_decode_loop_ok(self):
        code = ToricCode3D(3, 3, 3)
        decoder = SweepDecoder3D()

        error_pauli = Toric3DPauli(code)
        """
        sites = [
            (0, 0, 0, 0), (1, 1, 0, 0), (0, 0, 1, 0), (1, 0, 0, 0)
        ]
        """
        sites = [(1, 0, 0), (2, 1, 0), (1, 2, 0), (0, 1, 0)]
        for site in sites:
            error_pauli.site('Z', site)
        assert find_sites(error_pauli) == set(sites)
        error = error_pauli.to_bsf()

        # Compute the syndrome.
        syndrome = bcommute(code.stabilizers, error)

        signs = decoder.get_initial_state(code, syndrome)
        """
        assert dict_where(signs) == {
            (0, 0, 0, 0), (0, 0, 0, 2), (0, 1, 0, 0), (0, 1, 0, 2),
            (1, 0, 0, 0), (1, 0, 0, 2), (1, 0, 1, 0), (1, 0, 1, 2),
            (2, 0, 1, 0), (2, 0, 2, 0), (2, 1, 0, 0), (2, 2, 0, 0)
        }
        """
        assert dict_where(signs) == {
            (1, 2, 1), (2, 1, 1), (1, 0, 1), (3, 1, 0), (1, 5, 0), (1, 0, 5),
            (0, 1, 5), (1, 2, 5), (2, 1, 5), (5, 1, 0), (1, 3, 0), (0, 1, 1)
        }

        reconstructed_syndrome = rebuild_syndrome(code, signs)
        assert np.all(
            reconstructed_syndrome[:code.n_k_d[0]] == syndrome[:code.n_k_d[0]]
        )

        correction = decoder.decode(code, syndrome)
        total_error = (error.todense() + correction) % 2

        assert np.all(bcommute(code.stabilizers, total_error) == 0)

    def test_oscillating_cycle_fail(self):
        code = ToricCode3D(3, 3, 3)
        decoder = SweepDecoder3D()

        error_pauli = Toric3DPauli(code)
        sites = edge_coords([
            (0, 0, 1, 0), (0, 0, 1, 1), (0, 0, 2, 0), (0, 0, 2, 1),
            (0, 0, 2, 2), (0, 1, 1, 2), (0, 2, 0, 0), (0, 2, 0, 1),
            (0, 2, 0, 2), (1, 1, 0, 1), (1, 1, 2, 0), (1, 1, 2, 2),
            (1, 2, 0, 0), (1, 2, 0, 1), (1, 2, 0, 2), (1, 2, 2, 0),
            (1, 2, 2, 1), (1, 2, 2, 2), (2, 1, 0, 0), (2, 1, 1, 1),
            (2, 1, 2, 1),
        ], code.size)
        assert len(set(sites)) == 21
        for site in sites:
            error_pauli.site('Z', site)
        error = error_pauli.to_bsf()

        syndrome = bcommute(code.stabilizers, error)

        # Signs array.
        signs = decoder.get_initial_state(code, syndrome)

        # Keep a copy of the initial signs array.
        start_signs = signs.copy()

        # Keep track of the correction to apply.
        correction = Toric3DPauli(code)

        # Sweep 3 times.
        for i_sweep in range(3):
            signs = decoder.sweep_move(signs, correction, code)

        # Back to the start again.
        assert np.all(signs == start_signs)

        # The total correction is trivial.
        assert np.all(bcommute(code.stabilizers, correction.to_bsf()) == 0)

        # The total error still is not in code space.
        total_error = (error + correction.to_bsf().toarray()) % 2
        assert np.any(bcommute(code.stabilizers, total_error) != 0)

    def test_never_ending_staircase_fails(self):
        code = ToricCode3D(3, 3, 3)
        decoder = SweepDecoder3D()

        # Weight-8 Z error that may start infinite loop in sweep decoder.
        error_pauli = Toric3DPauli(code)
        sites = edge_coords([
            (0, 0, 2, 2), (0, 1, 1, 1), (0, 2, 0, 2), (1, 0, 0, 0),
            (1, 1, 0, 2), (1, 2, 2, 1), (2, 1, 2, 1), (2, 2, 0, 0)
        ], code.size)
        for site in sites:
            error_pauli.site('Z', site)
        error = error_pauli.to_bsf()
        # assert error.sum() == 8

        # Compute the syndrome and make sure it's nontrivial.
        syndrome = bcommute(code.stabilizers, error)
        assert np.any(syndrome)

        # Check face X stabilizer syndrome measurements.
        expected_syndrome_faces = face_coords([
            (0, 0, 0, 0), (0, 0, 0, 2), (0, 1, 0, 1), (0, 1, 0, 2),
            (0, 1, 1, 1), (0, 1, 2, 1), (0, 2, 0, 0), (0, 2, 2, 1),
            (1, 0, 2, 2), (1, 1, 0, 0), (1, 1, 1, 0), (1, 1, 1, 1),
            (1, 1, 2, 1), (1, 2, 0, 0), (1, 2, 0, 1), (1, 2, 0, 2),
            (2, 0, 0, 0), (2, 0, 0, 2), (2, 0, 1, 2), (2, 0, 2, 2),
            (2, 1, 0, 1), (2, 1, 0, 2), (2, 1, 1, 1), (2, 1, 2, 1),
            (2, 2, 0, 0), (2, 2, 0, 2), (2, 2, 2, 1), (2, 2, 2, 2)
        ], code.size)
        expected_signs = {k: 0 for k in code.face_index}
        for k in expected_syndrome_faces:
            expected_signs[k] = 1
        expected_syndrome = rebuild_syndrome(
            code, expected_signs
        )
        assert np.all(syndrome == expected_syndrome)
        """
        assert np.all(
            np.array(expected_syndrome_faces).T
            == np.where(syndrome[:code.n_k_d[0]].reshape(3, 3, 3, 3))
        )
        """

        # Attempt to perform decoding.
        correction = decoder.decode(code, syndrome)

        total_error = (error + correction.todense()) % 2

        # Assert that decoding has failed.
        assert np.any(bcommute(code.stabilizers, total_error))

    @pytest.mark.skip(reason='sparse')
    def test_sweep_move_two_edges(self):
        code = ToricCode3D(3, 3, 3)
        decoder = SweepDecoder3D()

        error = Toric3DPauli(code)
        error.site('Z', (0, 1, 0))
        error.site('Z', (1, 0, 0))

        syndrome = bcommute(code.stabilizers, error.to_bsf())

        correction = Toric3DPauli(code)

        # Syndrome from errors on x edge and y edge on vertex (0, 0, 0).
        signs = np.zeros((3, 3, 3, 3), dtype=np.uint)
        signs[1, 1, 1, 1] = 1
        signs[1, 1, 1, 0] = 1
        signs[0, 1, 1, 1] = 1
        signs[0, 1, 1, 0] = 1
        signs[2, 1, 0, 1] = 1
        signs[2, 0, 1, 1] = 1
        n_faces = code.n_k_d[0]
        assert np.all(syndrome[:n_faces].reshape(signs.shape) == signs)

        # Expected signs after one sweep.
        expected_signs_1 = np.zeros((3, 3, 3, 3), dtype=np.uint)
        expected_signs_1[2, 1, 0, 1] = 1
        expected_signs_1[2, 0, 1, 1] = 1
        expected_signs_1[0, 1, 0, 1] = 1
        expected_signs_1[0, 1, 0, 0] = 1
        expected_signs_1[1, 0, 1, 1] = 1
        expected_signs_1[1, 0, 1, 0] = 1
        signs_1 = decoder.sweep_move(signs, correction)
        assert np.all(expected_signs_1 == signs_1)

        # Expected signs after two sweeps, should be all gone.
        signs_2 = decoder.sweep_move(signs_1, correction)
        assert np.all(signs_2 == 0)

        expected_correction = Toric3DPauli(code)
        expected_correction.site('Z', (2, 1, 1, 1))
        expected_correction.site('Z', (0, 0, 1, 1))
        expected_correction.site('Z', (1, 1, 0, 1))
        expected_correction.site('Z', (2, 1, 1, 0))

        # Only need to compare the Z block because sweep only corrects Z block
        # anyway.
        correction_edges = set(
            map(tuple, np.array(np.where(correction._zs)).T)
        )
        expected_correction_edges = set(
            map(tuple, np.array(np.where(expected_correction._zs)).T)
        )

        assert correction_edges == expected_correction_edges
        assert np.all(correction._zs == expected_correction._zs)


def find_sites(error_pauli):
    """List of sites where Pauli has support over."""
    return set([
        location
        for location, index in error_pauli.code.qubit_index.items()
        if index in np.where(error_pauli._zs.toarray()[0])[0]
    ])


def rebuild_syndrome(code, signs):
    reconstructed_syndrome = np.zeros(code.stabilizers.shape[0], dtype=np.uint)
    for location, index in code.face_index.items():
        if signs[location]:
            reconstructed_syndrome[index] = 1
    return reconstructed_syndrome
