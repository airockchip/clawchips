# Third-party notices

ClawChips is distributed under the MIT License. The `RKClawServer/` source
tree is also MIT-licensed. Bundled or referenced components retain their
upstream licenses:

| Component | Source | Revision | License | Distribution in this repository |
| --- | --- | --- | --- | --- |
| RKClawServer | <https://github.com/airockchip/RKClawServer> | `v0.3.2-source.1` | MIT | Full public source |
| XGrammar | <https://github.com/mlc-ai/xgrammar> | `557becfb64c503ae9c04344b0047661f43f44320` | Apache-2.0 | Expanded source under `RKClawServer/native/3rdparty/xgrammar` |
| RKNN3 Tokenizer | <https://github.com/airockchip/rknn3-model-zoo/tree/main/tokenizer> | `174e44c77230735b1458946debb62b3982c1ee58` | Apache-2.0 | Header and Linux aarch64/x86_64 static libraries |
| Nanobot | <https://github.com/HKUDS/nanobot> | RK branch commit recorded in `release-manifest.yaml` | MIT | Referenced by the product package; source is not copied here |

The imported RKClawServer tree includes the applicable license texts and
Tokenizer build provenance. XGrammar also includes its own nested third-party
license files. Models, RKNN3 Toolkit Lite, Nanobot packages, and board-side
Skills are not redistributed by this Git repository and remain subject to
their own release terms.
