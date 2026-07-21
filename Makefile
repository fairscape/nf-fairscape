# Build the plugin
assemble:
	./gradlew assemble

clean:
	rm -rf .nextflow*
	rm -rf work
	rm -rf build
	./gradlew clean

# Run plugin unit tests
test:
	./gradlew test

# Install the plugin into local nextflow plugins dir
install:
	./gradlew install

# End-to-end: install, run the example pipeline, validate the emitted crate
# against the fairscape_models schema (set FAIRSCAPE_MODELS to the repo path
# if fairscape_models is not installed in your python environment)
FAIRSCAPE_MODELS ?= ../../../fairscape_models
verify: install
	cd nf-fairscape-test && rm -rf results work .nextflow* \
		&& nextflow run . -plugins nf-fairscape@$$(cat ../VERSION) \
		&& PYTHONPATH=$(FAIRSCAPE_MODELS) python3 validate_crate.py results/ro-crate-metadata.json

# Publish the plugin
release:
	./gradlew releasePluginIfNotExists
