import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import '../data/api_client.dart';

class AlertasPage extends StatefulWidget {
  const AlertasPage({super.key, required this.api});
  final ApiClient api;
  @override
  State<AlertasPage> createState() => _State();
}

class _State extends State<AlertasPage> {
  final alertas = <Map<String, dynamic>>[];
  WebSocketChannel? canal;
  String conexao = 'desconectado';

  @override
  void initState() {
    super.initState();
    _carregarHistorico();
    _conectar();
  }

  Future<void> _carregarHistorico() async {
    try {
      final r = await widget.api.enviar('GET', '/alertas');
      setState(() => alertas.addAll((jsonDecode(r.body)['itens'] as List).cast<Map<String, dynamic>>()));
    } catch (_) {}
  }

  void _conectar() {
    if (widget.api.token == null) { setState(() => conexao = 'faça login para receber alertas em tempo real'); return; }
    final url = widget.api.baseUrl.replaceFirst('http', 'ws');
    canal = WebSocketChannel.connect(Uri.parse('$url/ws/alertas?token=${widget.api.token}'));
    setState(() => conexao = 'ao vivo');
    canal!.stream.listen((m) {
      final j = jsonDecode(m as String) as Map<String, dynamic>;
      if (j['evento'] == 'alerta') setState(() => alertas.insert(0, j));
    }, onError: (_) => setState(() => conexao = 'conexão perdida'),
       onDone: () { if (mounted) { setState(() => conexao = 'reconectando...'); Future.delayed(const Duration(seconds: 5), _conectar); } });
  }

  @override
  void dispose() { canal?.sink.close(); super.dispose(); }

  @override
  Widget build(BuildContext context) => Column(children: [
        ListTile(dense: true, leading: const Icon(Icons.wifi_tethering), title: Text('Alertas: $conexao')),
        Expanded(child: ListView(children: [
          for (final a in alertas)
            ListTile(
              leading: Icon(Icons.warning, color: a['nivel'] == 'ALTO' ? Colors.red : Colors.amber),
              title: Text(a['mensagem']), subtitle: Text('${a['tipo']} - ${a['criado_em']}'),
            ),
        ])),
      ]);
}
