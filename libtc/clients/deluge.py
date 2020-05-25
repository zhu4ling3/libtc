import base64
import hashlib
import os

from datetime import datetime
from pathlib import Path

import pytz
from deluge_client import DelugeRPCClient
from deluge_client.client import DelugeClientException

from ..bencode import bencode
from ..exceptions import FailedToExecuteException
from ..torrent import TorrentData, TorrentState
from ..baseclient import BaseClient
from ..utils import map_existing_files, has_minimum_expected_data, calculate_minimum_expected_data


class DelugeClient(BaseClient):
    identifier = "deluge"

    keys = [
        "name",
        "progress",
        "download_location",
        "state",
        "total_size",
        "time_added",
        "total_uploaded",
        "tracker_host",
        "upload_payload_rate",
        "download_payload_rate",
        "label",
    ]

    def __init__(self, host, port, username, password, session_path=None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.session_path = Path(session_path)

    @property
    def client(self):
        return DelugeRPCClient(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            decode_utf8=True,
        )

    def _fetch_list_result(self, filter):
        result = []
        try:
            with self.client as client:
                torrents = client.core.get_torrents_status(filter, self.keys)
        except (DelugeClientException, ConnectionError, OSError):
            raise FailedToExecuteException()
        for infohash, torrent_data in torrents.items():
            if torrent_data["state"] in ["Seeding", "Downloading"]:
                state = TorrentState.ACTIVE
            elif torrent_data["state"] in ["Error"]:
                state = TorrentState.ERROR
            else:
                state = TorrentState.STOPPED

            print(torrent_data)
            result.append(
                TorrentData(
                    infohash,
                    torrent_data["name"],
                    torrent_data["total_size"],
                    state,
                    torrent_data["progress"],
                    torrent_data["total_uploaded"],
                    datetime.utcfromtimestamp(torrent_data["time_added"]).astimezone(
                        pytz.UTC
                    ),
                    torrent_data["tracker_host"],
                    torrent_data["upload_payload_rate"],
                    torrent_data["download_payload_rate"],
                    torrent_data.get("label", ""),
                )
            )
        return result

    def list(self):
        return self._fetch_list_result({})

    def list_active(self):
        return self._fetch_list_result({"state": "Active"})

    def start(self, infohash):
        try:
            with self.client as client:
                client.core.resume_torrent(infohash)
        except (DelugeClientException, ConnectionError, OSError):
            raise FailedToExecuteException()

    def stop(self, infohash):
        try:
            with self.client as client:
                client.core.pause_torrent(infohash)
        except (DelugeClientException, ConnectionError, OSError):
            raise FailedToExecuteException()

    def test_connection(self):
        try:
            with self.client as client:
                return client.core.get_free_space() is not None
        except (DelugeClientException, ConnectionError, OSError):
            return False

    def add(self, torrent, destination_path, fast_resume=False, add_name_to_folder=True, minimum_expected_data="none"):
        current_expected_data = calculate_minimum_expected_data(torrent, destination_path, add_name_to_folder)
        if not has_minimum_expected_data(minimum_expected_data, current_expected_data):
            raise FailedToExecuteException(f"Minimum expected data not reached, wanted {minimum_expected_data} actual {current_expected_data}")
        destination_path = destination_path.resolve()
        encoded_torrent = base64.b64encode(bencode(torrent))
        infohash = hashlib.sha1(bencode(torrent[b'info'])).hexdigest()
        options = {
            'download_location': str(destination_path),
            'seed_mode': fast_resume
        }
        if not add_name_to_folder:
            files = map_existing_files(torrent, destination_path, add_name_to_folder=False)
            mapped_files = {}
            for i, (fp, f, size, exists) in enumerate(files):
                mapped_files[i] = str(f)
            options['mapped_files'] = mapped_files

        try:
            with self.client as client:
                result = client.core.add_torrent_file('torrent.torrent', encoded_torrent, options)
        except (DelugeClientException, ConnectionError, OSError):
            raise FailedToExecuteException()

        if result != infohash:
            raise FailedToExecuteException()

    def remove(self, infohash):
        try:
            with self.client as client:
                client.core.remove_torrent(infohash, False)
        except (DelugeClientException, ConnectionError, OSError):
            raise FailedToExecuteException()

    def retrieve_torrentfile(self, infohash):
        torrent_path = self.session_path / "state" / f"{infohash}.torrent"
        if not torrent_path.is_file():
            raise FailedToExecuteException()
        return torrent_path.read_bytes()