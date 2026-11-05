"""Build a clean offline ZIP. Never include an existing database or local caches."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '2'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    import server
    with tempfile.TemporaryDirectory(prefix='aero-package-') as temp:
        project = Path(temp) / ('aero-DB-offline-v' + VERSION)
        project.mkdir()
        for name in ('.gitattributes', 'server.py', 'index.html', 'seed.json', 'start.bat', 'start.command', 'start.sh', 'README.md', 'tests/test_assets.py', 'tools/build_offline_package.py', 'requirements-postgresql.txt', 'requirements-mysql.txt'):
            target = project / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
            if name.endswith('.bat'):
                target.write_bytes(target.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        (project / 'data').mkdir()
        docs = project / 'docs';docs.mkdir()
        (docs / '素材批量导入模板.csv').write_bytes(('\ufeff' + ','.join(label for label,key in server.CSV_FIELDS) + '\r\n').encode('utf-8'))
        sample = dict(id='', title='组织绩效研究示例', content='示例正文第一段。\n示例正文第二段，包含逗号和"引号"。', source='示例内部资料', domain='战略与组织', type='专题研究', kind='段落素材', status='待审核', date='2026-10-15', owner='', tag='组织绩效', tag_source='offline_rule')
        (docs / '素材批量导入示例.csv').write_bytes(server.csv_text([sample]).encode('utf-8'))
        (project / '升级说明.txt').write_text('本包为离线内网版 V2，支持 SQLite 演示、PostgreSQL 测试和 MySQL 正式环境。\n共享部署前请按 README.md 配置数据库驱动、连接参数、监听地址及允许访问的内网主机。\n切换到 PostgreSQL/MySQL 不会自动迁移旧 SQLite 数据；请先备份并安排数据迁移。\nCSV备份只包含素材，完整备份须在停服后复制整个data目录。\n数据库驱动需通过离线 wheelhouse 安装，参阅 requirements-postgresql.txt 或 requirements-mysql.txt。\n',encoding='utf-8')
        files = sorted(p for p in project.rglob('*') if p.is_file())
        (project / 'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(project).as_posix()+'\n' for p in files), encoding='utf-8')
        archive = args.output_dir / (project.name + '.zip')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            for p in sorted(project.rglob('*')):
                if p.is_file(): z.write(p, p.relative_to(project.parent))
                elif p == project / 'data': z.write(p, p.relative_to(project.parent))
        with zipfile.ZipFile(archive) as z: assert z.testzip() is None
        print(archive)
        print('SHA256:', hashlib.sha256(archive.read_bytes()).hexdigest())


if __name__ == '__main__': main()
