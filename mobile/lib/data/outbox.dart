import 'dart:convert';
import 'package:path/path.dart';
import 'package:sqflite/sqflite.dart';
import 'package:uuid/uuid.dart';

enum Estado { pendente, sincronizado, erro }

class ItemFila {
  ItemFila(this.chave, this.payload, this.estado, this.ultimoErro);
  final String chave;
  final Map<String, dynamic> payload;
  final Estado estado;
  final String? ultimoErro;
}

/// Fila local (SQLite) de ocorrências criadas, inclusive sem internet.
class Outbox {
  Database? _db;
  Future<Database> get db async => _db ??= await openDatabase(
        join(await getDatabasesPath(), 'outbox.db'),
        version: 1,
        onCreate: (d, _) => d.execute('CREATE TABLE fila('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, chave TEXT UNIQUE NOT NULL,'
            'payload TEXT NOT NULL, estado TEXT NOT NULL DEFAULT "pendente",'
            'ultimo_erro TEXT)'),
      );

  Future<String> enfileirar(Map<String, dynamic> payload) async {
    final chave = const Uuid().v4(); // vira o Idempotency-Key
    await (await db).insert('fila', {'chave': chave, 'payload': jsonEncode(payload)});
    return chave;
  }

  Future<List<ItemFila>> pendentes() async {
    final rs = await (await db).query('fila', where: 'estado = ?', whereArgs: ['pendente'], orderBy: 'id');
    return rs.map(_item).toList();
  }

  Future<List<ItemFila>> todos() async =>
      (await (await db).query('fila', orderBy: 'id DESC')).map(_item).toList();

  Future<void> marcar(String chave, Estado e, {String? erro}) async =>
      (await db).update('fila', {'estado': e.name, 'ultimo_erro': erro}, where: 'chave = ?', whereArgs: [chave]);

  ItemFila _item(Map<String, Object?> r) => ItemFila(r['chave'] as String,
      jsonDecode(r['payload'] as String), Estado.values.byName(r['estado'] as String), r['ultimo_erro'] as String?);
}
