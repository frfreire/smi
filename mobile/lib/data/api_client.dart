import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'package:http/http.dart' as http;

class SessaoExpirada implements Exception {}
class FalhaDeRede implements Exception {
  final String motivo;
  FalhaDeRede(this.motivo);
  @override
  String toString() => 'FalhaDeRede($motivo)';
}
class CircuitoAberto implements Exception {}

/// Cliente HTTP com timeout, retry com backoff exponencial + jitter e circuit breaker.
class ApiClient {
  ApiClient(this.baseUrl, {http.Client? cliente})
      : _http = cliente ?? http.Client();

  final String baseUrl;
  final http.Client _http;
  String? token;
  static const _timeout = Duration(seconds: 8);
  static const _maxTentativas = 4;
  static const _limiarCircuito = 5;
  static const _pausaCircuito = Duration(seconds: 30);
  final _rng = Random();
  int _falhasSeguidas = 0;
  DateTime _abertoAte = DateTime.fromMillisecondsSinceEpoch(0);

  Duration _espera(int n) {
    final teto = min(8000, 500 * pow(2, n).toInt());
    return Duration(milliseconds: _rng.nextInt(teto + 1)); // jitter total
  }

  Future<http.Response> enviar(String metodo, String caminho,
      {Map<String, dynamic>? corpo, Map<String, String>? cabecalhos}) async {
    if (DateTime.now().isBefore(_abertoAte)) throw CircuitoAberto();
    final h = {
      'content-type': 'application/json',
      if (token != null) 'authorization': 'Bearer $token',
      ...?cabecalhos,
    };
    Object? ultimo;
    for (var n = 0; n < _maxTentativas; n++) {
      try {
        final req = http.Request(metodo, Uri.parse('$baseUrl$caminho'))
          ..headers.addAll(h);
        if (corpo != null) req.body = jsonEncode(corpo);
        final r = await http.Response.fromStream(await _http.send(req).timeout(_timeout));
        if (r.statusCode == 401) throw SessaoExpirada();
        if (r.statusCode < 500 && r.statusCode != 429) {
          _falhasSeguidas = 0;
          return r;
        }
        ultimo = FalhaDeRede('HTTP ${r.statusCode}');
      } on SessaoExpirada {
        rethrow;
      } on TimeoutException {
        ultimo = FalhaDeRede('timeout');
      } on http.ClientException catch (e) {
        ultimo = FalhaDeRede(e.message);
      }
      _falhasSeguidas++;
      if (_falhasSeguidas >= _limiarCircuito) {
        _abertoAte = DateTime.now().add(_pausaCircuito);
        break;
      }
      if (n < _maxTentativas - 1) await Future.delayed(_espera(n));
    }
    throw ultimo as FalhaDeRede;
  }

  Future<void> login(String email, String senha) async {
    final r = await enviar('POST', '/auth/login', corpo: {'email': email, 'senha': senha});
    if (r.statusCode != 200) throw Exception(jsonDecode(r.body)['mensagem']);
    token = jsonDecode(r.body)['access_token'];
  }
}
