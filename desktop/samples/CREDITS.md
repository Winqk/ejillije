# Записи барабанов

Звуки в этой папке собраны инструментом `desktop/tools/build_samples.py` из открытых библиотек.
Из каждой взяты нужные удары: микрофоны смешаны по настройкам авторов, выбраны 3–4 градации силы удара
с вариантами, обрезана тишина, выровнена громкость, сохранено в OGG Vorbis 44,1 кГц.

| Папка | Библиотека | Авторы | Лицензия |
|---|---|---|---|
| `rusty/` | [Big Rusty Drums](https://github.com/sfzinstruments/karoryfer.big-rusty-drums) | Karoryfer Samples | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `virtuosity/` | [Virtuosity Drums](https://github.com/sfzinstruments/virtuosity_drums) | Versilian Studios, Karoryfer Samples | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `frankensnare/` | [Frankensnare](https://github.com/sfzinstruments/karoryfer.frankensnare) | Karoryfer Samples | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `drs/` | [DRSKit](https://github.com/sfzinstruments/DrumGizmo.DRSKit) (DrumGizmo) | Lars Muldjord, Bent Bisballe Nyeng; SFZ-версия — kinwie | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |

`drs/china` взята из Big Rusty Drums (CC0). Файлы в `drs/` — производная работа от DRSKit, распространяется
по лицензии CC BY 4.0 с указанием авторов выше; изменения: сведение микрофонов, обрезка, нормализация громкости,
перекодирование, понижение строя дубля тома (`tom_lo`).

Жанры «Хип-хоп» и «Электроника» звучат синтезом (`desktop/sounds.py`), записей для них нет.
