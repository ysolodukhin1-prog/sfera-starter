"""Remove source-instance login wording from the neutral UI templates."""
from pathlib import Path
import json
root=Path(__file__).resolve().parents[1]
changes={
 '/api/access/logout':'/logout',
 'Права TREND остаются обязательными. Разрешение в Сфера не выдаёт доступ к данным TREND.':'Доступ определяется пользователем и разрешениями этого экземпляра Сферы.',
 'Профили из материнского графа · planned':'Профили экспертов этого клиента',
 'Доступ использует вашу учётную запись ТРЕНДа и права на контур CLIENT.':'Используй собственную учётную запись этого экземпляра Сферы.',
 'Войти через ТРЕНД':'Войти в Сфера',
 'https://github.com/ysolodukhin1-prog/galactica-sfera-plugin/blob/main/INSTALL_RU.md':'/galactica/INSTALL_RU.md',
 'Администратору TREND нужно открыть эту учётную запись и включить CLIENT → Сфера CLIENT → «Контекст и знания».':'Администратор этого экземпляра должен проверить пользователя и его права.',
 'Вход единый через TREND.':'Вход самостоятельный, через этот экземпляр Сферы.',
 'TREND':'Сфера','ТРЕНДа':'Сферы','ТРЕНД':'Сфера',
}
for folder in ['templates/ui','instance/runtime/ui']:
 for p in (root/folder).glob('*.js'):
  s=p.read_text(encoding='utf-8')
  for old,new in changes.items():s=s.replace(old,new)
  p.write_text(s,encoding='utf-8',newline='\n')
if (root/'instance/config.json').exists():
 config=json.loads((root/'instance/config.json').read_text(encoding='utf-8'))
 p=root/'templates/knowledge_acl_admin.py';s=p.read_text(encoding='utf-8').replace('TREND','Сферы')
 p.write_text(s,encoding='utf-8',newline='\n')
 (root/'instance/runtime/knowledge_acl_admin.py').write_text(s.replace('@@PROJECT_ID@@',config['project_id']),encoding='utf-8',newline='\n')
