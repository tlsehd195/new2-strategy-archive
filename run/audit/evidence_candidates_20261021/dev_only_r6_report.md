# Evolution report — context `C2-f64e792160a0222650f2`

Derived from the ledgers by `run/discovery/evolution_report.py`. Not a strategy verdict.

| stage | count |
|---|---|
| registered specifications | 894 ({'seed': 705, 'evolution': 189}) |
| search pool (seed + search) | 894 |
| DEV evaluated / OK / screen pass | 894 / 894 / 64 |
| validation batches / members | 0 / 0 |
| validation outcomes | {} |
| failure classes | {} |
| statuses | {'REJECTED': 737, 'INCONCLUSIVE': 93, 'RESEARCH_ONLY': 64} |
| outcomes (directive vocabulary) | {'PENDING': 64, 'FAIL_COST': 247, 'UNDETERMINED_POWER': 93, 'FAIL_STAT': 490} |
| outcomes by stage | {'dev_screen': {'PENDING': 64, 'FAIL_COST': 247, 'UNDETERMINED_POWER': 93, 'FAIL_STAT': 490}, 'validation': {}} |
| dedup decisions | {'behaviour_equal': 30, 'exact_definition': 15} |
| survivors | 0 |
| locked TEST attempts / exposed uses | 0 / 0 |

## Validation members

| strategy | outcome | class | status | per symbol n / gross / cost / net (bp) |
|---|---|---|---|---|

## Duplicate detection

| round | kind | proposed | representative | operator | reason |
|---|---|---|---|---|---|
| 1 | behaviour_equal | `S-ea1be04fa0e3be1f` | `S-202b2284faa1e9ac` | crossover | behaviourally identical (normal form of the conditions) |
| 1 | behaviour_equal | `S-7c1f8e64afaa6fc4` | `S-1a3a2db7ab18831d` | crossover | behaviourally identical (normal form of the conditions) |
| 2 | behaviour_equal | `S-0d7f17648693c1b9` | `S-202b2284faa1e9ac` | change_risk | differs only in risk settings, which no evaluation stage reads |
| 2 | behaviour_equal | `S-bd2f89e5a0b47b3f` | `S-6d1f375a260fc295` | crossover | behaviourally identical (normal form of the conditions) |
| 2 | behaviour_equal | `S-777ff681a210bcf4` | `S-2d59854060280dc5` | crossover | behaviourally identical (normal form of the conditions) |
| 2 | behaviour_equal | `S-18f26c15e0484421` | `S-ce2892b62e1979b0` | crossover | behaviourally identical (normal form of the conditions) |
| 4 | behaviour_equal | `S-51254a923035faf5` | `S-b5e5df1c18d5a57d` | change_risk | differs only in risk settings, which no evaluation stage reads |
| 4 | exact_definition | `S-8722469acaaa2b50` | `S-8722469acaaa2b50` | split_symbol | same canonical definition (strategy id) as a known strategy |
| 4 | behaviour_equal | `S-5863e8473a4e3db1` | `S-391396a9487de780` | change_risk | differs only in risk settings, which no evaluation stage reads |
| 4 | behaviour_equal | `S-f9e79b614e588b37` | `S-1f3e32adca82bc9f` | crossover | behaviourally identical (normal form of the conditions) |
| 4 | behaviour_equal | `S-e40f87e05dc905ee` | `S-6a94aee8965abd43` | crossover | behaviourally identical (normal form of the conditions) |
| 5 | exact_definition | `S-16c639004f17edf2` | `S-16c639004f17edf2` | tweak_threshold | same canonical definition (strategy id) as a known strategy |
| 5 | behaviour_equal | `S-96fe155d841c381a` | `S-5c9462f937550dac` | change_risk | differs only in risk settings, which no evaluation stage reads |
| 5 | behaviour_equal | `S-b1bc7693b378118c` | `S-8722469acaaa2b50` | change_risk | differs only in risk settings, which no evaluation stage reads |
| 5 | exact_definition | `S-a0ee2ba7abb5c6af` | `S-a0ee2ba7abb5c6af` | tweak_threshold | same canonical definition (strategy id) as a known strategy |
| 5 | exact_definition | `S-05f8eb8423661277` | `S-05f8eb8423661277` | tweak_param | same canonical definition (strategy id) as a known strategy |
| 5 | behaviour_equal | `S-91d8177aeccdceff` | `S-af5b9301777d21a5` | crossover | behaviourally identical (normal form of the conditions) |
| 5 | exact_definition | `S-ab8c4ae7bf335b9d` | `S-ab8c4ae7bf335b9d` | crossover | same canonical definition (strategy id) as a known strategy |
| 5 | behaviour_equal | `S-91d8177aeccdceff` | `S-af5b9301777d21a5` | crossover | behaviourally identical (normal form of the conditions) |
| 5 | behaviour_equal | `S-7c1f8e64afaa6fc4` | `S-1a3a2db7ab18831d` | crossover | behaviourally identical (normal form of the conditions) |
| 6 | behaviour_equal | `S-63e8669aaf890b98` | `S-1a3a2db7ab18831d` | change_risk | differs only in risk settings, which no evaluation stage reads |
| 6 | behaviour_equal | `S-91d8177aeccdceff` | `S-af5b9301777d21a5` | crossover | behaviourally identical (normal form of the conditions) |
| 6 | behaviour_equal | `S-6af0ca806efc420e` | `S-ab8c4ae7bf335b9d` | crossover | behaviourally identical (normal form of the conditions) |
| 6 | behaviour_equal | `S-68c8bbc08d5c0b27` | `S-5a47c0baf0100e04` | crossover | behaviourally identical (normal form of the conditions) |
| 6 | exact_definition | `S-3342fd89afbc3a26` | `S-3342fd89afbc3a26` | crossover | same canonical definition (strategy id) as a known strategy |
| 7 | exact_definition | `S-6e5d594fd0af0bbe` | `S-6e5d594fd0af0bbe` | change_horizon | same canonical definition (strategy id) as a known strategy |
| 7 | exact_definition | `S-b809d908d4d0391a` | `S-b809d908d4d0391a` | remove_condition | same canonical definition (strategy id) as a known strategy |
| 7 | behaviour_equal | `S-8198b9cf93e5fedb` | `S-391396a9487de780` | crossover | behaviourally identical (normal form of the conditions) |
| 7 | behaviour_equal | `S-016ecfef8fde5224` | `S-8722469acaaa2b50` | crossover | behaviourally identical (normal form of the conditions) |
| 7 | behaviour_equal | `S-8d460640a6647e15` | `S-181c15b4139efdd3` | crossover | behaviourally identical (normal form of the conditions) |
| 7 | exact_definition | `S-ab8c4ae7bf335b9d` | `S-ab8c4ae7bf335b9d` | crossover | same canonical definition (strategy id) as a known strategy |
| 8 | exact_definition | `S-1a3a2db7ab18831d` | `S-1a3a2db7ab18831d` | change_horizon | same canonical definition (strategy id) as a known strategy |
| 8 | exact_definition | `S-bc62b843361277d5` | `S-bc62b843361277d5` | tweak_param | same canonical definition (strategy id) as a known strategy |
| 8 | behaviour_equal | `S-4b7c91de0502fc6c` | `S-0fce3f9e7c6060fc` | change_risk | differs only in risk settings, which no evaluation stage reads |
| 8 | exact_definition | `S-391396a9487de780` | `S-391396a9487de780` | change_horizon | same canonical definition (strategy id) as a known strategy |
| 8 | exact_definition | `S-6e5d594fd0af0bbe` | `S-6e5d594fd0af0bbe` | change_horizon | same canonical definition (strategy id) as a known strategy |
| 8 | behaviour_equal | `S-0eb92b15dd7cbd36` | `S-e06d4b747cf33dfc` | crossover | behaviourally identical (normal form of the conditions) |
| 8 | exact_definition | `S-ab8c4ae7bf335b9d` | `S-ab8c4ae7bf335b9d` | crossover | same canonical definition (strategy id) as a known strategy |
| 8 | behaviour_equal | `S-8d460640a6647e15` | `S-181c15b4139efdd3` | crossover | behaviourally identical (normal form of the conditions) |
| 8 | exact_definition | `S-ac77f093ebc7d471` | `S-ac77f093ebc7d471` | crossover | same canonical definition (strategy id) as a known strategy |
| 8 | behaviour_equal | `S-5e6fc0b83d98d7eb` | `S-ac77f093ebc7d471` | crossover | behaviourally identical (normal form of the conditions) |
| 8 | behaviour_equal | `S-b1b60750406a963e` | `S-db25d24ca1ca4c96` | crossover | behaviourally identical (normal form of the conditions) |
| 8 | behaviour_equal | `S-8198b9cf93e5fedb` | `S-391396a9487de780` | crossover | behaviourally identical (normal form of the conditions) |
| 8 | behaviour_equal | `S-fe7ce788fee43d9f` | `S-209ce2dd7db8d919` | crossover | behaviourally identical (normal form of the conditions) |
| 8 | behaviour_equal | `S-fe7ce788fee43d9f` | `S-209ce2dd7db8d919` | crossover | behaviourally identical (normal form of the conditions) |
