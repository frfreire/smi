import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import '../data/outbox.dart';
import '../sync/sync_service.dart';

class NovaOcorrenciaPage extends StatefulWidget {
  const NovaOcorrenciaPage({super.key, required this.outbox, required this.sync});
  final Outbox outbox;
  final SyncService sync;
  @override
  State<NovaOcorrenciaPage> createState() => _State();
}

class _State extends State<NovaOcorrenciaPage> {
  final desc = TextEditingController(), lat = TextEditingController(), lon = TextEditingController();
  int severidade = 3;
  String? aviso;

  Future<void> _usarGps() async {
    try {
      var p = await Geolocator.checkPermission();
      if (p == LocationPermission.denied) p = await Geolocator.requestPermission();
      if (p == LocationPermission.denied || p == LocationPermission.deniedForever) {
        setState(() => aviso = 'Permissão de localização negada. Informe as coordenadas manualmente.');
        return;
      }
      final pos = await Geolocator.getCurrentPosition().timeout(const Duration(seconds: 10));
      setState(() { lat.text = pos.latitude.toStringAsFixed(5); lon.text = pos.longitude.toStringAsFixed(5); aviso = null; });
    } catch (_) {
      setState(() => aviso = 'GPS indisponível. Informe as coordenadas manualmente.');
    }
  }

  Future<void> _salvar() async {
    final la = double.tryParse(lat.text), lo = double.tryParse(lon.text);
    if (desc.text.trim().length < 3 || la == null || lo == null) {
      setState(() => aviso = 'Preencha a descrição e a localização.');
      return;
    }
    await widget.outbox.enfileirar({
      'tipo': 'ALAGAMENTO', 'descricao': desc.text.trim(), 'lat': la, 'lon': lo,
      'severidade': severidade, 'criada_em_cliente': DateTime.now().toUtc().toIso8601String(),
    });
    desc.clear();
    setState(() => aviso = 'Relato salvo. Enviando...');
    widget.sync.sincronizar(); // se estiver offline, permanece na fila
  }

  @override
  Widget build(BuildContext context) => ListView(padding: const EdgeInsets.all(16), children: [
        TextField(controller: desc, maxLength: 500, decoration: const InputDecoration(labelText: 'O que está acontecendo?')),
        Row(children: [
          Expanded(child: TextField(controller: lat, keyboardType: const TextInputType.numberWithOptions(signed: true, decimal: true), decoration: const InputDecoration(labelText: 'Latitude'))),
          const SizedBox(width: 12),
          Expanded(child: TextField(controller: lon, keyboardType: const TextInputType.numberWithOptions(signed: true, decimal: true), decoration: const InputDecoration(labelText: 'Longitude'))),
        ]),
        TextButton.icon(onPressed: _usarGps, icon: const Icon(Icons.my_location), label: const Text('Usar minha localização')),
        Text('Gravidade: $severidade'),
        Slider(value: severidade.toDouble(), min: 1, max: 5, divisions: 4, onChanged: (v) => setState(() => severidade = v.round())),
        FilledButton(onPressed: _salvar, child: const Text('Enviar relato')),
        if (aviso != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(aviso!)),
        const Divider(height: 32),
        const Text('Fila local', style: TextStyle(fontWeight: FontWeight.bold)),
        FutureBuilder<List<ItemFila>>(
          future: widget.outbox.todos(),
          builder: (c, s) => Column(children: [
            for (final i in s.data ?? [])
              ListTile(dense: true, title: Text(i.payload['descricao']),
                  subtitle: Text(i.estado.name + (i.ultimoErro != null ? ' - ${i.ultimoErro}' : ''))),
          ]),
        ),
      ]);
}
