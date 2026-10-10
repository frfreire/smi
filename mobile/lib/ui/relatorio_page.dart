import 'dart:convert';
import 'package:flutter/material.dart';
import '../data/api_client.dart';
import '../data/outbox.dart';

class RelatorioPage extends StatelessWidget {
  const RelatorioPage({super.key, required this.api, required this.outbox});
  final ApiClient api;
  final Outbox outbox;

  Future<Map<String, dynamic>> _carregar() async =>
      jsonDecode((await api.enviar('GET', '/relatorios/resumo?periodo=7d')).body);

  @override
  Widget build(BuildContext context) => FutureBuilder<Map<String, dynamic>>(
        future: _carregar(),
        builder: (c, s) {
          if (s.hasError) return const Center(child: Text('Resumo indisponível no momento.'));
          if (!s.hasData) return const Center(child: CircularProgressIndicator());
          final d = s.data!;
          return ListView(padding: const EdgeInsets.all(16), children: [
            Text('Últimos 7 dias: ${d['total']} relatos, ${d['alertas']} alertas',
                style: Theme.of(c).textTheme.titleMedium),
            const SizedBox(height: 12),
            for (final r in d['regioes_criticas'])
              ListTile(title: Text('Região ${r['regiao']}'), trailing: Text('${r['total']} relatos'),
                  subtitle: Text('Gravidade máxima ${r['severidade_max']}')),
          ]);
        },
      );
}
