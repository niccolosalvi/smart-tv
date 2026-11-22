"""Create an UNSIGNED receiver widget; Samsung TV deployment still requires signing."""
import argparse
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def package(output):
    source=ROOT/'tv-receiver'
    ET.parse(source/'config.xml')
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in ('config.xml','index.html','receiver.js'):archive.write(source/name,name)
    return output

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/smart-tv-receiver-unsigned.wgt')
    args=parser.parse_args()
    print('Unsigned widget: '+str(package(args.output).resolve()))
