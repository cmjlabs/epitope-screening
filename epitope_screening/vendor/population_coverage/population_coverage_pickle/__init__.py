import gzip
import pickle
from pathlib import Path

# Load only the trusted, bundled IEDB frequency snapshot.
with gzip.open(Path(__file__).with_name("population_genotype_map.p.gz"), "rb") as stream:
    population_coverage = pickle.load(stream)
    country_ethnicity = pickle.load(stream)
    ethnicity = pickle.load(stream)
