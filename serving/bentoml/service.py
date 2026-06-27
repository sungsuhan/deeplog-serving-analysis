"""BentoML service placeholder for DeepLog."""

import bentoml


@bentoml.env(infer_pip_packages=True)
@bentoml.artifacts([])
class DeepLogService(bentoml.BentoService):

    @bentoml.api(input=bentoml.io.JSON(), batchable=False)
    def predict(self, parsed_json):
        return {"prediction": "placeholder"}
