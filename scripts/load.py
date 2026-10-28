# Download the STT En QuartzNet15x5 model and the LibriSpeech dev-clean clips
import pathlib
import tarfile
import urllib.request

CHECKPOINT = "https://api.ngc.nvidia.com/v2/models/nvidia/nemo/stt_en_quartznet15x5/versions/1.0.0rc1/files/stt_en_quartznet15x5.nemo"
LIBRISPEECH = "https://www.openslr.org/resources/12/{}.tar.gz"

# Download to a .partial file first, so an interrupted download is not reused
def fetch(url, path):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        urllib.request.urlretrieve(url, partial)
        partial.replace(path)
    return path

def librispeech(split):
    if not pathlib.Path("data/LibriSpeech", split).is_dir():
        with tarfile.open(fetch(LIBRISPEECH.format(split), pathlib.Path(f"data/{split}.tar.gz"))) as archive:
            archive.extractall("data", filter="data")

def load():
    fetch(CHECKPOINT, pathlib.Path("data/quartznet.nemo"))
    librispeech("dev-clean")
