import os, sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "toolbox/src"))
sys.path.insert(0, src_path)

from progsnap2.spec.gen.gen_client import generate_ts_methods
from progsnap2.spec.gen.gen_enums import generate_enums_for_spec
from progsnap2.spec.spec_definition import ProgSnap2Spec
import pyperclip

if __name__ == "__main__":
    schema_path = os.path.join(os.path.dirname(__file__), "src/provena/progsnap2-provena.yaml")

    # Load schema
    schema = ProgSnap2Spec.from_yaml(schema_path)

    # Right now there's only one ground-truth enums file, which is generated
    # from a spec at compile time rather than runtime, we have to overwrite the
    # original PS2 enums with the current spec. Not ideal, but necessary until
    # we figure out a better way, since the server uses these Enums to generate
    # the API spec.
    out = generate_enums_for_spec(schema)
    with open("toolbox/src/progsnap2/spec/enums.py", "w", encoding='utf-8') as f:
        f.write(out)

    # Generate basic TypeScript client code for each event in this spec.
    out = generate_ts_methods(schema)
    pyperclip.copy(out)

