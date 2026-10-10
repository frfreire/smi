import 'package:flutter/material.dart';
import 'data/api_client.dart';
import 'data/outbox.dart';
import 'sync/sync_service.dart';
import 'ui/alertas_page.dart';
import 'ui/mapa_page.dart';
import 'ui/nova_ocorrencia_page.dart';
import 'ui/relatorio_page.dart';

// Emulador Android acessa o host em 10.0.2.2; simulador iOS usa localhost.
const apiUrl = String.fromEnvironment('API_URL', defaultValue: 'http://10.0.2.2:8000');

void main() => runApp(const App());

class App extends StatelessWidget {
  const App({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Alagamentos',
        theme: ThemeData(colorSchemeSeed: Colors.blue, useMaterial3: true),
        darkTheme: ThemeData(colorSchemeSeed: Colors.blue, brightness: Brightness.dark, useMaterial3: true),
        home: const Home(),
      );
}

class Home extends StatefulWidget {
  const Home({super.key});
  @override
  State<Home> createState() => _HomeState();
}

class _HomeState extends State<Home> {
  final api = ApiClient(apiUrl);
  final outbox = Outbox();
  late final SyncService sync = SyncService(api, outbox);
  int aba = 0;
  bool logado = false;

  @override
  void initState() {
    super.initState();
    sync.status.stream.listen((m) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
    });
  }

  Future<void> _login() async {
    final email = TextEditingController(), senha = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Entrar'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(controller: email, decoration: const InputDecoration(labelText: 'E-mail')),
          TextField(controller: senha, obscureText: true, decoration: const InputDecoration(labelText: 'Senha')),
        ]),
        actions: [TextButton(onPressed: () => Navigator.pop(c, true), child: const Text('Entrar'))],
      ),
    );
    if (ok != true) return;
    try {
      await api.login(email.text, senha.text);
      setState(() => logado = true);
      sync.sincronizar();
    } on FalhaDeRede {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Não foi possível conectar ao servidor.')));
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final paginas = [
      MapaPage(api: api),
      NovaOcorrenciaPage(outbox: outbox, sync: sync),
      AlertasPage(api: api),
      RelatorioPage(api: api, outbox: outbox),
    ];
    return Scaffold(
      appBar: AppBar(title: const Text('Alagamentos Urbanos'), actions: [
        IconButton(icon: Icon(logado ? Icons.verified_user : Icons.login), onPressed: _login),
        IconButton(icon: const Icon(Icons.sync), onPressed: sync.sincronizar),
      ]),
      body: paginas[aba],
      bottomNavigationBar: NavigationBar(
        selectedIndex: aba,
        onDestinationSelected: (i) => setState(() => aba = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.map), label: 'Mapa'),
          NavigationDestination(icon: Icon(Icons.add_location_alt), label: 'Relatar'),
          NavigationDestination(icon: Icon(Icons.warning_amber), label: 'Alertas'),
          NavigationDestination(icon: Icon(Icons.bar_chart), label: 'Resumo'),
        ],
      ),
    );
  }
}
