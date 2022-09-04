import numpy as np
from typing import Optional, Tuple
from pymatching import Matching
from panqec.decoders import BaseDecoder
from panqec.codes import StabilizerCode
from panqec.error_models import BaseErrorModel


class MatchingDecoder(BaseDecoder):
    """Matching decoder for 2D Toric Code, based on PyMatching"""

    label = 'Toric 2D Matching'
    allowed_codes = ["Toric2DCode", "Planar2DCode", "RotatedPlanar2DCode"]

    def __init__(self,
                 code: StabilizerCode,
                 error_model: BaseErrorModel,
                 error_rate: float,
                 error_type: Optional[str] = None):
        """Constructor for the MatchingDecoder class

        Parameters
        ----------
        code : StabilizerCode
            Code used by the decoder
        error_model: BaseErrorModel
            Error model used by the decoder (to find the weights)
        error_rate: int, optional
            Error rate used by the decoder (to find the weights)
        error_type: str, optional
            Determines which type of errors (X or Z) to decode.
            Can take the values "X", "Z", or None if we want to
            decode all errors
        """
        super().__init__(code, error_model, error_rate)

        if error_type not in ["X", "Z", None]:
            raise ValueError("Argument 'error_type' has to be 'X', 'Z'"
                             f"or None, not {error_type}")

        self.error_type = error_type
        wx, wz = self.get_weights()

        if error_type is None or error_type == "X":
            self.matcher_x = Matching(self.code.Hz, spacelike_weights=wx)
        if error_type is None or error_type == "Z":
            self.matcher_z = Matching(self.code.Hx, spacelike_weights=wz)

    def decode(self, syndrome: np.ndarray, **kwargs) -> np.ndarray:
        """Get X corrections given code and measured syndrome."""

        # Initialize correction as full bsf.
        correction = np.zeros(2*self.code.n, dtype=np.uint)

        # Keep only the vertex Z measurement syndrome, discard the rest.
        if self.error_type is None or self.error_type == "X":
            syndromes_z = self.code.extract_z_syndrome(syndrome)
            correction_x = self.matcher_x.decode(syndromes_z,
                                                 num_neighbours=None)
            correction[:self.code.n] = correction_x
        if self.error_type is None or self.error_type == "Z":
            syndromes_x = self.code.extract_x_syndrome(syndrome)
            correction_z = self.matcher_z.decode(syndromes_x,
                                                 num_neighbours=None)
            correction[self.code.n:] = correction_z

        return correction

    def get_weights(self, eps=1e-10) -> Tuple[np.ndarray, np.ndarray]:
        """Get MWPM weights for deformed Pauli noise."""

        pi, px, py, pz = self.error_model.probability_distribution(
            self.code, self.error_rate
        )

        total_p_x = px + py
        total_p_z = pz + py

        weights_x = -np.log(
            (total_p_x + eps) / (1 - total_p_x + eps)
        )
        weights_z = -np.log(
            (total_p_z + eps) / (1 - total_p_z + eps)
        )

        return weights_x, weights_z
