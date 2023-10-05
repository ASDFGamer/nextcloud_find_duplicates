#!/usr/bin/env python3
# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring

from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from functools import cached_property
import json
import datetime
from pathlib import Path
import subprocess
from typing import Any
from urllib.parse import quote

class Log:
    @staticmethod
    def info(message: str):
        print(f"[INFO {datetime.datetime.now().isoformat()}]: {message}")

@dataclass
class NextcloudInfo:
    domain: str
    user: str

    @property
    def user_file_path(self) -> Path :
        return Path(f"/var/www/html/data/{self.user}/files/")

    @property
    def base_file_url(self) -> str:
        return f"{self.domain}/apps/files/?dir=/"

    def create_link(self, path: Path) -> str:
        if path.is_relative_to(self.user_file_path):
            path = path.relative_to(self.user_file_path)
        if path.is_dir():
            return self._create_folder_link(path)
        parent_link = self._create_folder_link(path.parent)
        return parent_link + "/" + path.name

    def _create_folder_link(self, path:Path) -> str:
        return f"[{path}]({self.base_file_url}{quote(str(path))})"


class MatchSet:
    def __init__(self, json_data: dict[str, Any]) -> None:
        self._file_size: int = json_data["fileSize"]
        self._file_list: list[Path] = self._get_file_list(json_data)

    @staticmethod
    def _get_file_list(json_data: dict[str, Any]) -> list[Path]:
        result: list[Path] = []
        for str_path in json_data["fileList"]:
            result.append(Path(str_path["filePath"]))
        return result

    @property
    def file_size(self) -> int:
        return self._file_size

    @property
    def file_list(self) -> list[Path]:
        return self._file_list

    @cached_property
    def directory_list(self) -> list[Path]:
        return [x.parent for x in self._file_list]

@dataclass(frozen=True)
class DuplicateInFolder:
    in_duplicate_folder: Path
    other_paths: frozenset[Path]
    size: int

class JDupesOutput:

    def __init__(self, nextcloud_info: NextcloudInfo) -> None:
        self._nextcloud_info = nextcloud_info
        json_data = self._execute_jdupes(nextcloud_info)
        self.version: str = json_data["jdupesVersion"]
        version_date_str: str = json_data["jdupesVersionDate"]
        self.version_date: datetime.date = datetime.date.fromisoformat(version_date_str)
        self.command_line: str = json_data["commandLine"]
        self.extension_flags: str = json_data["extensionFlags"]
        self.match_sets: set[MatchSet] = self._get_matchsets(json_data)
        self._folder_cache: dict[Path, set[MatchSet]] = self._create_folder_cache(self.match_sets)

    @staticmethod
    def _execute_jdupes(nextcloud_info: NextcloudInfo) -> dict[str, Any]:
        args = f"jdupes -j -r {nextcloud_info.user_file_path}"
        Log.info(f"Starting JDupes with arguments: {args}")
        json_str: str = subprocess.run(
            ["jdupes" ,"-j", "-r", nextcloud_info.user_file_path ],
            stdout=subprocess.PIPE,
            check=True).stdout.decode('utf-8')
        Log.info("Executed JDupes")
        return json.loads(json_str)

    @staticmethod
    def _get_matchsets(json_data: dict[str, Any]) -> set[MatchSet]:
        result:set[MatchSet] = set()
        for match_set in json_data["matchSets"]:
            result.add(MatchSet(match_set))
        return result

    @staticmethod
    def _create_folder_cache(match_sets: set[MatchSet]) -> dict[Path, set[MatchSet]]:
        result: dict[Path, set[MatchSet]] = {}
        for match_set in match_sets:
            for folder in match_set.directory_list:
                if folder not in result:
                    result[folder] = set()
                result[folder].add(match_set)
        return result

    @cached_property
    def total_size(self) -> int:
        return sum(map( lambda x: x.file_size, self.match_sets))

    @cached_property
    def folders_with_duplicates(self) -> set[Path]:
        result: set[Path] = set()
        for match_set in self.match_sets:
            for file_path in match_set.file_list:
                result.add(file_path.parent)
        return result

    def duplicates_in_folder(self, folder: Path) -> set[DuplicateInFolder]:
        result: set[DuplicateInFolder] = set()
        for match_set in self._folder_cache[folder]:
            for file_path in match_set.file_list:
                if file_path.parent == folder:
                    other_files: set[Path] = set(match_set.file_list.copy())
                    other_files.remove(file_path)
                    other_files_frozen = frozenset(other_files)
                    result.add(DuplicateInFolder(file_path,other_files_frozen,match_set.file_size))
        return result


    def to_markdown(self)-> str:
        Log.info("Parse Folders with duplicates")
        duplicate_folders: list[tuple[Path, set[DuplicateInFolder], list[Path]]] = []
        for duplicate_folder in self.folders_with_duplicates:
            all_files: list[Path] =  list(duplicate_folder.iterdir())
            duplicates_in_folder: set[DuplicateInFolder] = self.duplicates_in_folder(duplicate_folder)
            duplicate_files: set[Path] = set()
            for duplicate_match_set in duplicates_in_folder:
                duplicate_files.add(duplicate_match_set.in_duplicate_folder)
            not_duplicate_files: list[Path] = [x for x in all_files if x not in duplicate_files]
            duplicate_folders.append((duplicate_folder,duplicates_in_folder, not_duplicate_files))
        Log.info("Create Markdown output")
        duplicate_folders=  sorted(duplicate_folders, key= lambda x: len(x[2]))
        result: list[str] = []
        result.append(f"# Duplikate von {self._nextcloud_info.user}")
        result.append(
            f"Es gibt {len(self.match_sets)} Duplikate in {len(self.folders_with_duplicates)} Ordnern.  ")
        result.append(f"Diese sind insgesamt {humansize(self.total_size)} groß.")
        result.append("## Alle Ordner mit Duplikaten")
        
        for entry in duplicate_folders:
            duplicate_folder = entry[0]
            duplicates_in_folder = entry[1]
            not_duplicate_files = entry[2]
            folder_link = self._nextcloud_info.create_link(
                duplicate_folder)
            result.append(f"## Ordner mit Duplikaten: {folder_link}")
            duplicate_folder_size= humansize(sum(map(lambda x: x.size,
                self.duplicates_in_folder(duplicate_folder))))
            result.append(f"Die Duplikate in diesem Ordner sind { duplicate_folder_size} groß  ")
            if len(not_duplicate_files) == 0:
                result.append("Dieser Ordner enthält nur Duplikate  ")

            result.append("### Duplizierte Dateien:")
            for duplicate_match_set in duplicates_in_folder:
                result.append(f"- {duplicate_match_set.in_duplicate_folder.relative_to(self._nextcloud_info.user_file_path)}  ")
                result.append(f"\tGröße: {humansize(duplicate_match_set.size)}  ")
                duplicate_files_str ="\t- "+"  \n\t- ".join(
                    [self._nextcloud_info.create_link(x) for x in duplicate_match_set.other_paths])
                result.append("\tAndere(r) Ordner:  ")
                result.append(f"{duplicate_files_str}  ")
            if len(not_duplicate_files) > 0:
                result.append("### Nicht duplizierte Dateien")
                for not_duplicate_file in not_duplicate_files:
                    result.append(f"- {not_duplicate_file.relative_to(self._nextcloud_info.user_file_path)}")
        return "\n".join(result)

def humansize(nbytes: float) -> str:
    suffixes: list[str] = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    i = 0
    while nbytes >= 1024 and i < len(suffixes)-1:
        nbytes /= 1024.
        i += 1
    size = (f'{nbytes:.2f}').rstrip('0').rstrip('.')
    return f'{size}  {suffixes[i]}'

def parse_args() -> Namespace:
    parser = ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("domain")
    return parser.parse_args()

def write_to_nextcloud(nextcloud_info: NextcloudInfo, file_content: str) -> None:
    filepath = Path(nextcloud_info.user_file_path, "Duplikate.md")
    Log.info(f"Writing duplicates file to {filepath}")
    with open(filepath, mode="w", encoding="UTF-8") as open_file:
        open_file.write(file_content)
    Log.info("Running shallow rescan of homefolder to detect new file")
    subprocess.run([
        "/var/www/html/occ", "files:scan", "-p", 
        f"{nextcloud_info.user}/files", "--shallow", 
        nextcloud_info.user], check=True)

def main() -> None:
    args = parse_args()
    nextcloud_info = NextcloudInfo(user= args.username,domain= args.domain)
    jdupes_outpt: JDupesOutput = JDupesOutput(nextcloud_info)
    markdown = jdupes_outpt.to_markdown()
    write_to_nextcloud(nextcloud_info, markdown)
if __name__ == "__main__":
    main()
