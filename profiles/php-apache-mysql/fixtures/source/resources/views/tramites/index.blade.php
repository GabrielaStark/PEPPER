@extends('layouts.app')
@section('title', 'Trámites de la dependencia')
@section('content')
<h1>Trámites</h1>
<h2>Bandeja de la dependencia</h2>
@can('crear-tramites')
  <a href="{{ route('tramites.create') }}">Nuevo trámite</a>
@endcan
<form method="GET" action="{{ route('tramites.index') }}" id="filtros">
  <label for="estado">Estado</label>
  <select name="estado" id="estado"><option value="">Todos</option></select>
  <label for="folio">Folio</label>
  <input type="text" name="folio" id="folio">
  <input type="submit" value="Buscar">
</form>
@role('supervisor')
  <p>{{ __('mensajes.confirmar_turnado') }}</p>
@endrole
@if (session('ok')) <div class="alerta">{{ session('ok') }}</div> @endif
<p>{{ __('mensajes.error_folio_duplicado') }}</p>
@include('tramites.partials.tabla')
@endsection
