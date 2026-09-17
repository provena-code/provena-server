# provena-server

## Overview

This is a FastAPI that records and serves data collected from a vscode extension for the purposes of helping to verify student programming work and collect research data.

It builds on two submodules:
* [provena-core](https://github.com/thomaswp/provena-core) (`provena` folder): contains the core typescript logic for building a provenance history from the logs, annotating each character with its original source.
* * **Note**: This is not yet used directly. Currently the clients are responsible for building provenance histories. It may be used in the future.
* [ProgSnapToolkit](https://github.com/CSSPLICE/ProgSnapToolkit) (`toolbox` folder): contains the logic for logging data in the ProgSnap2 format (only write logic). It relies on a .yaml file to define the format and uses that to structure the database.

It also connects with two other client repos:
* [provena-vscode](https://github.com/thomaswp/provena-vscode): A vs-code client that writes a student's history to the server.
* [provena-client](https://github.com/thomaswp/provena-client): A web client for the instructor interface that can read data from the server to show student histories.

## Structure

* `api`: contains the core server endpoints
* * `logging`: logging endpoints, called by the provena-vscode extension
* * `read`: instructor-facing endpoints, called by the provena-client webapp.
* `bridge`: Code for running the `provena-core` typescript logic. Not current used on the server side.
* `read_config.yaml`: Configuration for reading from the database (SQLite or MySQL). See `toolbox/README.md` for details
* * **Note**: This should be created using `read_config.example.yaml` if it does not already exist.
* `write_config.yaml`: Configuration for creating writing to the database (SQLite or MySQL). See `toolbox/README.md` for details. The read and write configuration files should match (they exist separately because the Toolkit this is built on handles logging and reading/analytics separately).
* * **Note**: This should be created using `write_config.example.yaml` if it does not already exist.
* `progsnap2-provena.yaml`: A yaml definition of the ProgSnap2 logging format used by Provena, which differs somewhat from the original.