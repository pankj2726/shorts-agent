"""
test_arena_cluster.py — Unit Tests for Arena Connection Cluster & Warm TLS Engine.
"""

import pytest
from unittest.mock import patch

from agent.modules.arena_cluster import ArenaClusterClient, ArenaAuthenticationError


def test_arena_cluster_client_auth_headers():
    with patch.dict("os.environ", {"ARENA_API_KEY": "test_arena_key"}):
        client = ArenaClusterClient()
        headers = client.get_headers()
        assert headers["Authorization"] == "Bearer test_arena_key"
        assert "ShortsAgent" in headers["User-Agent"]
        assert "pankajbhati140@gmail.com" in headers["X-Workspace-Identity"]
        assert headers["Connection"] == "keep-alive"


def test_arena_cluster_client_missing_key_raises():
    with patch.dict("os.environ", {"ARENA_API_KEY": ""}, clear=True):
        client = ArenaClusterClient()
        with pytest.raises(ArenaAuthenticationError):
            client.get_headers()


def test_arena_connection_cluster_singleton():
    client1 = ArenaClusterClient.get_instance()
    client2 = ArenaClusterClient.get_instance()
    assert client1 is client2
