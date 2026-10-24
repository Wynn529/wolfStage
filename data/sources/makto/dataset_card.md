---
annotations_creators:
- expert-generated
language:
- zh
- en
license:
- mit
multilinguality:
- multilingual
size_categories:
- n<50K
source_datasets:
- original
task_categories:
- text-generation
pretty_name: werewolf_game_reasoning
viewer: true
tags:
- instruction-tuning
- conversation
- game-play
dataset_info:
  features:
    - name: instruction
      dtype: string
    - name: prompt
      dtype: string
    - name: response
      dtype: string
    - name: meta
      dtype: string
  splits:
    - name: train_zh
    - name: train_en
    - name: action_zh
    - name: speech_zh
    - name: vote_zh
    - name: game_strategy_and_term

configs:
  - config_name: default
    data_files:
      - split: train_zh
        path: data/train_zh.parquet
      - split: train_en
        path: data/train_en.parquet
      - split: action_zh
        path: data/action_zh.parquet
      - split: speech_zh
        path: data/speech_zh.parquet
      - split: vote_zh
        path: data/vote_zh.parquet
      - split: game_strategy_and_term
        path: data/game_strategy_and_term.parquet
extra_files:
  - raw/
  - process_script/
  - README.md
---

# Werewolf Game Dataset

This repository contains a comprehensive dataset for the Werewolf game in paper [Multi-agent KTO: Reinforcing Strategic Interactions of Large Language Model in Language Game](https://arxiv.org/abs/2501.14225), including both raw game data and processed  multi-level instruction datasets.


## Dataset Structure

### Raw Data

The raw data is located in the `raw` folder. Each game consists of two files:

1. `event.json`: Contains the game regular record and thinking process data, including:
   - regular record:
      - Nocturnal action records of god roles and werewolves
      - Daytime speeches of players
      - Daytime votes of players
      - Game review
   - thinking process:
      - Speech summary including intended labels for others and voting intentions
      - Voting rationale

2. `note.json`: Contains the thinking process data, including:
   - Future strategies which includes action rationale and the target
   - Notes which includes a summary of the day's events


The file structure of one game:
```bash
├── seer_witch_guard
│   └── game_1
│       ├── event_en.json
│       ├── event_zh.json
│       ├── note_en.json
│       ├── note_zh.json
```

`_zh` indicates Chinese version (original), and `_en` indicates English version (Claude-translated).

**Note:** The English game data is automatically translated by ``Claude-3.5-Sonnet-V2``. If some of the data translation is missing, you can prompt LLM again to translate it by yourself.


### Processed Datasets

Based on the books, internet information and raw data, we've constructed three types of datasets for Supervised Fine-Tuning (SFT):

1. **Fundamental Game Comprehension**: Located in `game_strategy_and_term` folder
   - Compiled explanations of Werewolf game jargon extracted from books on the subject
   - Provides understanding of specialized terms used in the game

2. **Advanced Gaming Techniques**: Located in `game_strategy_and_term` folder
   - Strategies shared by experienced players
   - Offers guidance for various common game scenarios

3. **Authentic Gaming Behavior**: Located in `game_behavior` folder
   - Structured in a "think-before-respond" format for Actions, Speeches, and Votes
   - Includes a role prediction task, designed to predict each player's role at the end of each day based on known information.

## Data Processing

The scripts for processing the raw data are located in the `process_script` folder:

- `process_data.py`
- `utils.py`

To construct the dataset, run the following commands:

```bash
# For training data, use zh as default
python3 process_script/process_data.py --read_path './raw/train/7_player_game/seer_guard','./raw/train/7_player_game/seer_witch','./raw/train/9_player_game/guard_witch_seer','./raw/train/9_player_game/hunter_witch_seer' --language zh --save_path ./game_behavior --add_rolepred True

python3 process_script/csv_to_parquet.py --csv_paths ./game_behavior

# For test data
python3 process_script/process_data.py --read_path './raw/test/7_player_game/seer_guard','./raw/test/7_player_game/seer_witch','./raw/test/9_player_game/guard_witch_seer','./raw/test/9_player_game/hunter_witch_seer' --language zh --save_path ./test --add_rolepred True
```

When executing this script, the following parameters must be specified:
- `--read_path`: This is used to specify the path to read the raw data. 
- `--language`: Used to specify the language of the processed data. You may choose `zh` or `en`. The default is `zh`.
- `--save_path`: Used to specify the path where the processed data will be saved. 
- `--add_rolepred`: If this parameter is set to `True`, it means that the role prediction task will be added.

After executing the above command, multiple formats of data files will be generated in the specified save path `./game_behavior`, including `csv` and `parquet` formats:
- **Original Data Format Files**
  - `action.csv`, `speech.csv`, and `vote.csv` store the data related to actions, speeches, and votes in the game behavior in `csv` format respectively. 
- **Configuration File**
  - The `config.json` file is used to record some statistical information of processed instruction data, including the raw data path, the total number of different types of data and the detailed prompts length.
- **Role Distribution File**
  - The `roles_distribution.json` file records the distribution of roles in the game.


# Citation
```bibtex
@article{ye2025multi,
  title={Multi-agent KTO: Reinforcing Strategic Interactions of Large Language Model in Language Game},
  author={Ye, Rong and Zhang, Yongxin and Zhang, Yikai and Kuang, Haoyu and Wei, Zhongyu and Sun, Peng},
  journal={arXiv preprint arXiv:2501.14225},
  year={2025}
}
```
>>>>>>> 1824595 (Initial dataset upload)
