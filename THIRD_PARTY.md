# Сторонние компоненты

Skeebs использует сторонние библиотеки, которые сохраняют собственные лицензии и авторские права. Тексты уведомлений поставляются в папке `licenses` архива Windows.

| Компонент | Версия | Проект / исходники |
|---|---|---|
| CPython | 3.14.2 | https://www.python.org/downloads/release/python-3142/ |
| PySide6 / Shiboken | 6.10.1 | https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.10.1-src/ |
| Qt | 6.10.1 | https://download.qt.io/official_releases/qt/6.10/6.10.1/submodules/ |
| DXcam | 0.3.0 | https://github.com/ra1nty/DXcam |
| MSS | 10.2.0 | https://github.com/BoboTiG/python-mss |
| OpenCV Python | 5.0.0.93 | https://github.com/opencv/opencv-python |
| NumPy | 2.4.6 | https://github.com/numpy/numpy |
| SoundCard | 0.4.6 | https://github.com/bastibe/SoundCard |
| comtypes | 1.4.17 | https://github.com/enthought/comtypes |
| CFFI | 2.1.0 | https://cffi.readthedocs.io/ |
| PyInstaller | 6.20.0 | https://pyinstaller.org/en/stable/license.html |

Qt и PySide используются по условиям LGPLv3; исходные архивы соответствующих версий приложены к релизу отдельно. Копии LGPLv3 и GPLv3 включены в `licenses/Qt`. Библиотеки не изменялись. Qt загружается динамически из `_internal`; пользователь может заменить совместимые DLL. Условия Skeebs не ограничивают изменение этих библиотек и обратную разработку, необходимую для отладки таких изменений. Код приложения и инструкция сборки доступны в репозитории.

Сведения Qt: https://doc.qt.io/qtforpython-6.10/licenses.html и https://doc.qt.io/qt-6.10/licensing.html .

Дополнительные лицензии компонентов OpenCV, NumPy, Python и Qt сохранены в соответствующих подпапках. В комплекте может присутствовать код загрузчика PyInstaller, распространяемый по GPL с исключением для собранных приложений.

**FFmpeg не входит в публичный ZIP Skeebs.** Установите его отдельно с https://ffmpeg.org/download.html . Его лицензия зависит от выбранной сборки. Skeebs запускает FFmpeg отдельным процессом.
