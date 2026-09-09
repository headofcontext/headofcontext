#!/usr/bin/env bash
# Regenerate docs/authz-model.json from the DSL, copy it into the package (`hoc model load`
# ships it) and run the model tests. Needs docker only.
set -euo pipefail
cd "$(dirname "$0")/.."
docker run --rm -v "$PWD/docs:/docs" openfga/cli:v0.7.0 model transform --file /docs/authz-model.fga > docs/authz-model.json
cp docs/authz-model.json src/headofcontext/engines/openfga/authz-model.json
docker run --rm -v "$PWD/docs:/docs" openfga/cli:v0.7.0 model test --tests /docs/authz-model.fga.yaml
