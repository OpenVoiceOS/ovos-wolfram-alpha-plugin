"""
Unit tests for ovos-wolfram-alpha-plugin.

All network calls and OVOS config are mocked — no API key or daemon required.
"""
import os
import socket
import threading
import unittest
from unittest.mock import MagicMock, patch, mock_open

import requests
from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus

from ovos_wolfram_alpha_plugin import (
    NO_APPID_MESSAGE,
    MissingAppIdError,
    WolframAlphaApi,
    WolframAlphaRetrievalEngine,
    WolframAlphaToolbox,
    SearchWolframAlphaArgs,
    SearchWolframAlphaOutput,
)


# ---------------------------------------------------------------------------
# WolframAlphaApi
# ---------------------------------------------------------------------------

class TestWolframAlphaApi(unittest.TestCase):

    def setUp(self):
        self.api = WolframAlphaApi(key="TEST-KEY")

    def test_key_stored(self):
        self.assertEqual(self.api.key, "TEST-KEY")

    def test_no_key_means_no_request(self):
        api = WolframAlphaApi(key=None)
        with patch("ovos_wolfram_alpha_plugin.requests.get") as get, \
                self.assertRaises(MissingAppIdError) as raised:
            api.spoken("2+2", lat_lon=(0, 0))
        get.assert_not_called()
        self.assertIn("No Wolfram Alpha appid is configured", str(raised.exception))

    def _mock_get(self, text="answer"):
        m = MagicMock()
        m.text = text
        m.content = b"imagedata"
        return m

    def test_spoken_returns_text(self):
        with patch("ovos_wolfram_alpha_plugin.requests.get") as mock_get:
            mock_get.return_value = self._mock_get("The speed of light is 3×10^8 m/s.")
            result = self.api.spoken("speed of light", lat_lon=(0, 0))
        self.assertEqual(result, "The speed of light is 3×10^8 m/s.")

    def test_llm_returns_text(self):
        with patch("ovos_wolfram_alpha_plugin.requests.get") as mock_get:
            mock_get.return_value = self._mock_get("42")
            result = self.api.llm("meaning of life", lat_lon=(0, 0))
        self.assertEqual(result, "42")

    def test_full_results_returns_json(self):
        payload = {"queryresult": {"pods": []}}
        with patch("ovos_wolfram_alpha_plugin.requests.get") as mock_get:
            mock_get.return_value.json.return_value = payload
            result = self.api.full_results("mercury", lat_lon=(0, 0))
        self.assertEqual(result, payload)

    def test_get_image_writes_file_on_miss(self):
        with patch("ovos_wolfram_alpha_plugin.isfile", return_value=False), \
             patch("ovos_wolfram_alpha_plugin.requests.get") as mock_get, \
             patch("builtins.open", mock_open()) as mocked_open:
            mock_get.return_value.content = b"gif"
            path = self.api.get_image("mercury")
        self.assertTrue(path.endswith(".gif"))
        mocked_open.assert_called_once()

    def test_get_image_skips_download_on_hit(self):
        with patch("ovos_wolfram_alpha_plugin.isfile", return_value=True), \
             patch("ovos_wolfram_alpha_plugin.requests.get") as mock_get:
            self.api.get_image("mercury")
        mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# WolframAlphaRetrievalEngine
# ---------------------------------------------------------------------------

class TestWolframAlphaRetrievalEngine(unittest.TestCase):

    def _make_engine(self):
        with patch("ovos_wolfram_alpha_plugin.load_tx_plugin", return_value=None), \
             patch("ovos_wolfram_alpha_plugin.Configuration", return_value={}):
            return WolframAlphaRetrievalEngine(config={"appid": "TEST"})

    def test_query_returns_answer_and_score(self):
        engine = self._make_engine()
        engine.get_spoken_answer = MagicMock(return_value="Venus is the second planet.")
        with patch("ovos_wolfram_alpha_plugin.Configuration", return_value={}):
            result = engine.query("venus", lang="en")
        self.assertEqual(result, [("Venus is the second planet.", 0.9)])

    def test_query_returns_empty_when_no_answer(self):
        engine = self._make_engine()
        engine.get_spoken_answer = MagicMock(return_value=None)
        with patch("ovos_wolfram_alpha_plugin.Configuration", return_value={}):
            result = engine.query("xyzzy", lang="en")
        self.assertEqual(result, [])

    def test_get_spoken_answer_filters_bad_responses(self):
        engine = self._make_engine()
        engine.api = MagicMock()
        engine.api.spoken.return_value = "Wolfram Alpha did not understand your input"
        with patch("ovos_wolfram_alpha_plugin.Configuration", return_value={}):
            result = engine.get_spoken_answer("gibberish", lang="en")
        self.assertIsNone(result)

    def test_get_spoken_answer_returns_answer_for_english(self):
        engine = self._make_engine()
        engine.api = MagicMock()
        engine.api.spoken.return_value = "Mercury is the smallest planet."
        with patch("ovos_wolfram_alpha_plugin.Configuration", return_value={}):
            result = engine.get_spoken_answer("mercury", lang="en")
        self.assertEqual(result, "Mercury is the smallest planet.")

    def test_get_spoken_answer_translates_non_english(self):
        engine = self._make_engine()
        engine.translator = MagicMock()
        engine.translator.translate.side_effect = lambda q, target, source: f"[{target}]{q}"
        engine.api = MagicMock()
        engine.api.spoken.return_value = "Mercury is a planet."
        with patch("ovos_wolfram_alpha_plugin.Configuration", return_value={}):
            result = engine.get_spoken_answer("mercúrio", lang="pt")
        self.assertIn("pt", result)


# ---------------------------------------------------------------------------
# make_speakable
# ---------------------------------------------------------------------------

class TestMakeSpeakable(unittest.TestCase):

    def test_removes_human_parenthetical(self):
        text = "The height is 1.8 m (human: 5 ft 11 in)"
        result = WolframAlphaRetrievalEngine.make_speakable(text)
        self.assertNotIn("human:", result)

    def test_expands_unit_abbreviation(self):
        result = WolframAlphaRetrievalEngine.make_speakable("mass is 5 kg")
        self.assertIn("kilograms", result)

    def test_expands_cm_unit(self):
        result = WolframAlphaRetrievalEngine.make_speakable("height 180 cm")
        self.assertIn("centimeters", result)

    def test_noop_on_plain_text(self):
        text = "The answer is forty-two."
        result = WolframAlphaRetrievalEngine.make_speakable(text)
        self.assertEqual(result, text)


# ---------------------------------------------------------------------------
# WolframAlphaToolbox
# ---------------------------------------------------------------------------

class TestWolframAlphaToolbox(unittest.TestCase):

    def _make_toolbox(self):
        with patch("ovos_wolfram_alpha_plugin.WolframAlphaApi"):
            return WolframAlphaToolbox(config={"appid": "TEST"})

    def test_discover_tools_returns_one_tool(self):
        tb = self._make_toolbox()
        tools = tb.discover_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "search_wolfram_alpha")

    def test_search_wolfram_returns_output(self):
        tb = self._make_toolbox()
        tb.api = MagicMock()
        tb.api.llm.return_value = "42"
        args = SearchWolframAlphaArgs(query="meaning of life", units="metric")
        result = tb.search_wolfram(args)
        self.assertIsInstance(result, SearchWolframAlphaOutput)
        self.assertEqual(result.result, "42")

    def test_toolbox_id(self):
        self.assertEqual(WolframAlphaToolbox.toolbox_id, "ovos-wolfram-alpha-tools")

    def test_constructs_the_way_the_persona_server_loader_does(self):
        # ovos_persona_server.tools._load_toolboxes calls cls(config=cfg, bus=bus).
        # A plugin whose __init__ does not accept bus raises a TypeError there
        # and the loader only logs a warning, so the toolbox silently vanishes.
        with patch("ovos_wolfram_alpha_plugin.WolframAlphaApi"):
            cfg = {"appid": "TEST"}
            tb = WolframAlphaToolbox(config=cfg, bus=None)
        self.assertEqual(tb.config, cfg)
        self.assertTrue(tb.tools)

    def test_constructor_forwards_bus_so_bind_actually_runs(self):
        # Passing a bus to __init__ must reach ToolBox.bind(), so the toolbox
        # answers the OVOS-TOOLS-1 topics with real values: discover lists the
        # tools of this toolbox, and a call runs the tool and returns its result.
        bus = FakeBus()
        with patch("ovos_wolfram_alpha_plugin.WolframAlphaApi"):
            tb = WolframAlphaToolbox(config={"appid": "TEST"}, bus=bus)
        tb.api = MagicMock()
        tb.api.llm.return_value = "42"
        replies = []
        bus.on("ovos.persona.tools.discover.response", lambda m: replies.append(m.data))
        bus.on(f"ovos.persona.tools.{tb.toolbox_id}.call.response", lambda m: replies.append(m.data))

        bus.emit(Message("ovos.persona.tools.discover"))
        bus.emit(Message(f"ovos.persona.tools.{tb.toolbox_id}.call",
                         {"name": "search_wolfram_alpha",
                          "kwargs": {"query": "meaning of life", "units": "imperial"}}))

        discovered, called = replies
        self.assertEqual(discovered["toolbox_id"], "ovos-wolfram-alpha-tools")
        self.assertEqual([t["name"] for t in discovered["tools"]], ["search_wolfram_alpha"])
        self.assertEqual(called["result"]["result"], "42")
        tb.api.llm.assert_called_once_with(query="meaning of life", units="imperial")


# ---------------------------------------------------------------------------
# Plugin loading
# ---------------------------------------------------------------------------

class TestPluginLoading(unittest.TestCase):

    def test_imports(self):
        from ovos_wolfram_alpha_plugin import (
            WolframAlphaApi,
            WolframAlphaRetrievalEngine,
            WolframAlphaToolbox,
        )


# ---------------------------------------------------------------------------
# API errors
# ---------------------------------------------------------------------------

SECRET_APPID = "SECRET-APPID-123"
REASONS = {400: "Bad Request", 401: "Unauthorized", 403: "Forbidden"}


def _http_response(status: int, text: str) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.reason = REASONS.get(status, "Not Implemented")
    response._content = text.encode()
    # the real request URL carries the key and the user's location
    response.url = ("https://api.wolframalpha.com/v1/llm-api?input=2%2B2"
                    f"&geolocation=38.7,-9.1&appid={SECRET_APPID}")
    return response


class TestWolframAlphaApiErrors(unittest.TestCase):
    # The API answers a refused key with "403 Blocked request". That page is
    # an error, never an answer for the user or the model.

    def test_llm_and_spoken_raise_on_a_refused_request(self):
        # 400: missing key, 401: invalid key, 403: refused key
        api = WolframAlphaApi(key=SECRET_APPID)
        for status in (400, 401, 403):
            for call in (api.llm, api.spoken):
                with self.subTest(status=status, call=call.__name__), \
                        patch("ovos_wolfram_alpha_plugin.requests.get",
                              return_value=_http_response(status, "Blocked request")):
                    with self.assertRaises(requests.HTTPError) as raised:
                        call("2+2", lat_lon=(0, 0))
                    self.assertEqual(raised.exception.response.status_code, status)
                    self.assertNotIn(SECRET_APPID, str(raised.exception))
                    self.assertNotIn("38.7", str(raised.exception))

    def test_connection_errors_never_carry_the_key(self):
        # requests puts the full URL, query string included, into the text of
        # a connection error. The URL carries the appid and the geolocation.
        url = ("https://api.wolframalpha.com/v1/llm-api?input=2%2B2"
               f"&geolocation=38.7,-9.1&appid={SECRET_APPID}")
        api = WolframAlphaApi(key=SECRET_APPID)
        for error_type in (requests.ConnectionError, requests.ConnectTimeout, requests.exceptions.ProxyError):
            for call in (api.llm, api.spoken):
                error = error_type(f"HTTPSConnectionPool: Max retries exceeded with url: {url}")
                with self.subTest(error=error_type.__name__, call=call.__name__), \
                        patch("ovos_wolfram_alpha_plugin.requests.get", side_effect=error):
                    with self.assertRaises(error_type) as raised:
                        call("2+2", lat_lon=(38.7, -9.1))
                    chain, seen = [], raised.exception
                    while seen is not None:
                        chain.append(seen)
                        seen = seen.__cause__ or seen.__context__
                    for link in chain:
                        self.assertNotIn(SECRET_APPID, str(link))
                        self.assertNotIn(SECRET_APPID, repr(link))
                        self.assertNotIn("38.7", str(link))

    def test_engine_log_never_shows_the_key(self):
        refusals = [_http_response(401, "Invalid appid"),
                    requests.ConnectionError(
                        "Max retries exceeded with url: /v1/spoken?i=2%2B2"
                        f"&geolocation=38.7,-9.1&appid={SECRET_APPID}")]
        for refusal in refusals:
            with patch("ovos_wolfram_alpha_plugin.WolframAlphaApi"):
                engine = WolframAlphaRetrievalEngine(config={"appid": SECRET_APPID}, translator=MagicMock())
            engine.api = WolframAlphaApi(key=SECRET_APPID)
            get = {"return_value": refusal} if isinstance(refusal, requests.Response) \
                else {"side_effect": refusal}
            with self.subTest(refusal=type(refusal).__name__), \
                    patch("ovos_wolfram_alpha_plugin.requests.get", **get), \
                    patch.object(WolframAlphaApi, "_get_lat_lon", return_value=(38.7, -9.1)), \
                    patch("ovos_wolfram_alpha_plugin.LOG") as log:
                self.assertEqual(engine.query("what is 2+2", lang="en-us"), [])
            logged = " ".join(str(arg) for c in log.method_calls for arg in c.args)
            self.assertTrue(logged)
            self.assertNotIn(SECRET_APPID, logged)
            self.assertNotIn("38.7", logged)

    def test_llm_returns_the_no_answer_text(self):
        api = WolframAlphaApi(key="TEST")
        with patch("ovos_wolfram_alpha_plugin.requests.get",
                   return_value=_http_response(501, "Wolfram|Alpha did not understand your input")):
            self.assertEqual(api.llm("xyzzy", lat_lon=(0, 0)),
                             "Wolfram|Alpha did not understand your input")

    def test_engine_gives_no_answer_on_a_refused_request(self):
        with patch("ovos_wolfram_alpha_plugin.WolframAlphaApi"):
            engine = WolframAlphaRetrievalEngine(config={"appid": "TEST"}, translator=MagicMock())
        engine.api = WolframAlphaApi(key="TEST")
        with patch("ovos_wolfram_alpha_plugin.requests.get",
                   return_value=_http_response(403, "Blocked request")):
            self.assertEqual(engine.query("what is 2+2", lang="en-us"), [])

    def test_tool_call_reports_a_refused_request_as_an_error(self):
        bus = FakeBus()
        with patch("ovos_wolfram_alpha_plugin.WolframAlphaApi"):
            tb = WolframAlphaToolbox(config={"appid": "TEST"}, bus=bus)
        tb.api = WolframAlphaApi(key="TEST")
        replies = []
        bus.on(f"ovos.persona.tools.{tb.toolbox_id}.call.response", lambda m: replies.append(m.data))
        with patch("ovos_wolfram_alpha_plugin.requests.get",
                   return_value=_http_response(403, "Blocked request")), \
                patch.object(WolframAlphaApi, "_get_lat_lon", return_value=(0, 0)):
            bus.emit(Message(f"ovos.persona.tools.{tb.toolbox_id}.call",
                             {"name": "search_wolfram_alpha", "kwargs": {"query": "2+2"}}))
        self.assertNotIn("result", replies[0])
        self.assertIn("search_wolfram_alpha", replies[0]["error"])


class TestWolframAlphaStalledConnection(unittest.TestCase):
    # A server that accepts the connection and never answers must not hold the
    # caller forever: a bus handler that waits on it never answers again.

    def setUp(self):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(5)
        proxy = f"http://127.0.0.1:{self.server.getsockname()[1]}"
        self.env = patch.dict(os.environ, {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "NO_PROXY": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.server.close()

    def test_engine_gives_up_on_a_silent_server(self):
        with patch("ovos_wolfram_alpha_plugin.WolframAlphaApi"):
            engine = WolframAlphaRetrievalEngine(config={"appid": "TEST"}, translator=MagicMock())
        engine.api = WolframAlphaApi(key="TEST")
        engine.api.SPOKEN_TIMEOUT = 0.5
        result = {}
        with patch.object(WolframAlphaApi, "_get_lat_lon", return_value=(0, 0)):
            worker = threading.Thread(
                target=lambda: result.update(answer=engine.query("what is 2+2", lang="en-us")),
                daemon=True)
            worker.start()
            worker.join(10)
        self.assertFalse(worker.is_alive(), "the spoken request still waits after 10 s")
        self.assertEqual(result["answer"], [])

    def test_tool_call_gives_up_on_a_silent_server(self):
        bus = FakeBus()
        with patch("ovos_wolfram_alpha_plugin.WolframAlphaApi"):
            tb = WolframAlphaToolbox(config={"appid": "TEST"}, bus=bus)
        tb.api = WolframAlphaApi(key="TEST")
        tb.api.REQUEST_TIMEOUT = 0.5
        replies = []
        bus.on(f"ovos.persona.tools.{tb.toolbox_id}.call.response", lambda m: replies.append(m.data))
        call = Message(f"ovos.persona.tools.{tb.toolbox_id}.call",
                       {"name": "search_wolfram_alpha", "kwargs": {"query": "2+2"}})
        with patch.object(WolframAlphaApi, "_get_lat_lon", return_value=(0, 0)):
            worker = threading.Thread(target=bus.emit, args=(call,), daemon=True)
            worker.start()
            worker.join(10)
        self.assertFalse(worker.is_alive(), "the request still waits after 10 s")
        self.assertIn("error", replies[0])


class TestNoAppId(unittest.TestCase):

    def test_engine_without_appid_logs_and_gives_no_answer_without_a_request(self):
        with patch("ovos_wolfram_alpha_plugin.LOG") as log:
            engine = WolframAlphaRetrievalEngine(config={}, translator=MagicMock())
            self.assertIsNone(engine.api.key)
            self.assertIn("No Wolfram Alpha appid is configured", str(log.error.call_args_list[0]))
            with patch("ovos_wolfram_alpha_plugin.requests.get") as get, \
                    patch("ovos_wolfram_alpha_plugin.Configuration", return_value={}), \
                    patch.object(WolframAlphaApi, "_get_lat_lon", return_value=(0, 0)):
                self.assertEqual(engine.query("what is 2+2", lang="en-us"), [])
        get.assert_not_called()
        self.assertEqual(log.error.call_count, 2)

    def test_toolbox_without_appid_reports_an_error_without_a_request(self):
        bus = FakeBus()
        with patch("ovos_wolfram_alpha_plugin.LOG") as log:
            tb = WolframAlphaToolbox(config={}, bus=bus)
        self.assertIsNone(tb.api.key)
        log.error.assert_called_once_with(NO_APPID_MESSAGE)
        replies = []
        bus.on(f"ovos.persona.tools.{tb.toolbox_id}.call.response", lambda m: replies.append(m.data))
        with patch("ovos_wolfram_alpha_plugin.requests.get") as get, \
                patch.object(WolframAlphaApi, "_get_lat_lon", return_value=(0, 0)):
            bus.emit(Message(f"ovos.persona.tools.{tb.toolbox_id}.call",
                             {"name": "search_wolfram_alpha", "kwargs": {"query": "2+2"}}))
        get.assert_not_called()
        self.assertNotIn("result", replies[0])
        self.assertIn("search_wolfram_alpha", replies[0]["error"])
        with patch("ovos_wolfram_alpha_plugin.requests.get") as get, \
                patch.object(WolframAlphaApi, "_get_lat_lon", return_value=(0, 0)), \
                self.assertRaises(MissingAppIdError):
            tb.search_wolfram(SearchWolframAlphaArgs(query="2+2"))
        get.assert_not_called()


class TestWolframAlphaEndpointTimeouts(unittest.TestCase):

    def _timeout_passed_to_get(self, call):
        response = MagicMock(ok=True, text="ok")
        response.json.return_value = {}
        with patch("ovos_wolfram_alpha_plugin.requests.get", return_value=response) as get:
            call(WolframAlphaApi(key="TEST"))
        return get.call_args.kwargs["timeout"]

    def test_spoken_waits_10_seconds(self):
        self.assertEqual(self._timeout_passed_to_get(
            lambda api: api.spoken("2+2", lat_lon=(0, 0))), 10)

    def test_llm_waits_25_seconds(self):
        self.assertEqual(self._timeout_passed_to_get(
            lambda api: api.llm("2+2", lat_lon=(0, 0))), 25)

    def test_full_results_waits_25_seconds(self):
        self.assertEqual(self._timeout_passed_to_get(
            lambda api: api.full_results("2+2", lat_lon=(0, 0))), 25)


if __name__ == "__main__":
    unittest.main()
