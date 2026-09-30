<?php
namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;

class TramiteController extends Controller
{
    public function index(Request $request)
    {
        $tramites = DB::table('tramites as t')
            ->join('estados as e', 'e.id', '=', 't.estado_id')
            ->where('t.dependencia_id', $request->user()->dependencia_id)
            ->orderByDesc('t.created_at')->paginate(20);
        return view('tramites.index', compact('tramites'));
    }

    public function store(Request $request)
    {
        $datos = $request->validate(['folio' => 'required|unique:tramites', 'solicitante' => 'required']);
        DB::table('tramites')->insert($datos + ['estado_id' => 1, 'created_at' => now()]);
        return redirect()->route('tramites.index')->with('ok', __('mensajes.tramite_guardado'));
    }

    public function turnar($id)
    {
        Http::post('https://api.pagos.ejemplo.com/v2/verificar', ['tramite' => $id]);
        DB::table('tramites')->where('id', $id)->update(['estado_id' => 2]);
        return back();
    }
}
