from pathlib import Path
import json,re,base64,shutil,xml.etree.ElementTree as ET
app=Path(__file__).resolve().parents[1]; project=app.parent; dest=app/'public/assets'
for folder in ['wellio','icons','readiness']: (dest/folder).mkdir(parents=True,exist_ok=True)
source=project/'design/style-studies/assets/wellio'
shutil.copytree(source,dest/'wellio',dirs_exist_ok=True)
shutil.copy2(project/'design/style-studies/assets/wellio-logo.png',dest/'wellio-logo.png')
s=(project/'design/style-studies/snapshots/icon-comparison-readiness-2026-09-12.html').read_text()
readiness=json.loads(re.search(r'const readinessAssets=(\{.*?\});',s).group(1))
for key,data in readiness.items(): (dest/'readiness'/f'{key}.webp').write_bytes(base64.b64decode(data.split(',')[1]))
icons={}
def convert(e):
 attrs={k.replace('stroke-width','strokeWidth').replace('stroke-linecap','strokeLinecap').replace('stroke-linejoin','strokeLinejoin').replace('fill-rule','fillRule').replace('clip-rule','clipRule'):v for k,v in e.attrib.items()}
 for k in ['stroke']:
  if attrs.get(k,'').upper()=='#2B3A2B':attrs[k]='currentColor'
 return {'tag':e.tag.split('}')[-1],'attrs':attrs,'children':[convert(c) for c in e if not c.tag.endswith('title')]}
for p in (project/'wellio-product-svg').glob('icon-*.svg'):
 shutil.copy2(p,dest/'icons'/p.name)
 el=ET.parse(p).getroot();icons[p.stem.removeprefix('icon-')]=[convert(c) for c in el if not c.tag.endswith('title')]
(app/'src/components/icon-art.json').write_text(json.dumps(icons,separators=(',',':'))+'\n')
print('Copied selected artwork and 19 B icons; preserved source images.')
