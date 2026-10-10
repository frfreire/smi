import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';
import '../data/api_client.dart';

/// Mapa com os relatos colaborativos (gota) e, opcionalmente, os registros oficiais do
/// CEMADEN (triângulo), carregados pela API a partir do arquivo oficial de ocorrências.
class MapaPage extends StatefulWidget {
  const MapaPage({super.key, required this.api});
  final ApiClient api;
  @override
  State<MapaPage> createState() => _State();
}

class _Dados {
  _Dados(this.relatos, this.cemaden);
  final List<dynamic> relatos;
  final List<dynamic> cemaden;
}

class _State extends State<MapaPage> {
  late Future<_Dados> futuro = _carregar();
  bool mostrarCemaden = true;
  String uf = 'SP';

  Future<_Dados> _carregar() async {
    final r = await widget.api.enviar('GET', '/ocorrencias?page_size=100');
    final c = await widget.api.enviar('GET', '/cemaden/ocorrencias?tipologia=HIDRO&uf=$uf&page_size=200');
    return _Dados(jsonDecode(r.body)['itens'], jsonDecode(c.body)['itens']);
  }

  Color _cor(int sev) => Color.lerp(Colors.amber, Colors.red, (sev - 1) / 4)!;
  Color _corMagnitude(String m) =>
      switch (m) { 'GP' => Colors.red.shade900, 'MP' => Colors.deepOrange, _ => Colors.blueGrey };

  @override
  Widget build(BuildContext context) => FutureBuilder<_Dados>(
        future: futuro,
        builder: (c, s) {
          if (s.hasError) {
            return Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
              const Text('Não foi possível carregar o mapa.'),
              FilledButton(onPressed: () => setState(() => futuro = _carregar()), child: const Text('Tentar novamente')),
            ]));
          }
          if (!s.hasData) return const Center(child: CircularProgressIndicator());
          final d = s.data!;
          return Stack(children: [
            FlutterMap(
              options: const MapOptions(initialCenter: LatLng(-23.55, -46.63), initialZoom: 9),
              children: [
                TileLayer(urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', userAgentPackageName: 'br.unip.aps.alagamentos'),
                if (mostrarCemaden)
                  MarkerLayer(markers: [
                    for (final o in d.cemaden)
                      Marker(point: LatLng(o['lat'], o['lon']), width: 26, height: 26,
                          child: Tooltip(
                              message: '${o['municipio']} - ${o['data']} - magnitude ${o['magnitude']} (CEMADEN)',
                              child: Icon(Icons.change_history, color: _corMagnitude(o['magnitude'])))),
                  ]),
                MarkerLayer(markers: [
                  for (final o in d.relatos)
                    Marker(point: LatLng(o['lat'], o['lon']), width: 32, height: 32,
                        child: Icon(Icons.water_drop, color: _cor(o['severidade']))),
                ]),
              ],
            ),
            Positioned(top: 8, left: 8, right: 8, child: Wrap(spacing: 8, children: [
              FilterChip(label: const Text('CEMADEN'), selected: mostrarCemaden,
                  onSelected: (v) => setState(() => mostrarCemaden = v)),
              DropdownButton<String>(value: uf, items: [
                for (final u in ['SP', 'RJ', 'MG', 'PE', 'SC', 'ES', 'AM', 'BA'])
                  DropdownMenuItem(value: u, child: Text(u)),
              ], onChanged: (v) => setState(() { uf = v!; futuro = _carregar(); })),
            ])),
          ]);
        },
      );
}
