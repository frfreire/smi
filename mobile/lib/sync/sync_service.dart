import 'dart:async';
import 'dart:convert';
import 'package:connectivity_plus/connectivity_plus.dart';
import '../data/api_client.dart';
import '../data/outbox.dart';

/// Envia a fila ao servidor; roda ao reconectar e sob demanda.
class SyncService {
  SyncService(this.api, this.outbox) {
    _sub = Connectivity().onConnectivityChanged.listen((r) {
      if (!r.contains(ConnectivityResult.none)) sincronizar();
    });
  }
  final ApiClient api;
  final Outbox outbox;
  late final StreamSubscription _sub;
  bool _rodando = false;
  final status = StreamController<String>.broadcast();

  Future<void> sincronizar() async {
    if (_rodando) return;
    _rodando = true;
    try {
      for (final item in await outbox.pendentes()) {
        try {
          final r = await api.enviar('POST', '/ocorrencias',
              corpo: item.payload, cabecalhos: {'Idempotency-Key': item.chave});
          if (r.statusCode == 200 || r.statusCode == 201) {
            await outbox.marcar(item.chave, Estado.sincronizado);
          } else {
            final j = jsonDecode(r.body);
            await outbox.marcar(item.chave, Estado.erro, erro: jsonEncode(j['campos'] ?? j['mensagem']));
          }
        } on FalhaDeRede {
          status.add('Sem conexão com o servidor. Será reenviado depois.');
          break;
        } on CircuitoAberto {
          status.add('Servidor indisponível. Nova tentativa em instantes.');
          break;
        } on SessaoExpirada {
          status.add('Sessão expirada. Faça login novamente.');
          break;
        }
      }
    } finally {
      _rodando = false;
    }
  }

  void dispose() { _sub.cancel(); status.close(); }
}
