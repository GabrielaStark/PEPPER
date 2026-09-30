<table>
@foreach ($tramites as $t)
  <tr><td>{{ $t->folio }}</td><td>{{ $t->estado }}</td></tr>
@endforeach
</table>
