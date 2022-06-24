import json
from markovify import Text


def get_model():
    with open('speaking/markov.json') as f:
        model_json = json.load(f)

    model = Text.from_json(model_json)
    return model