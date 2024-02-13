import tomllib

CONFIG_PATH="config/config.toml"
WORLD_PATH="config/world.toml"

def data_from_file(path):
    with open(path, "rb") as f:
       return tomllib.load(f)