from a2ui_eval.seeds import load_seeds


def test_all_seeds_load():
    seeds = load_seeds()
    assert len(seeds) == 43
    for seed in seeds.values():
        assert seed.name and seed.description and seed.messages
