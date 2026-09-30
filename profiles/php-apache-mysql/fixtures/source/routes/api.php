<?php
use Illuminate\Support\Facades\Route;
use App\Http\Controllers\Api\TramiteApiController;

Route::middleware('auth:api')->group(function () {
    Route::get('/tramites/{folio}', [TramiteApiController::class, 'show']);
    Route::post('/tramites/{folio}/pago', [TramiteApiController::class, 'registrarPago']);
});
Route::get('/salud', function () { return ['ok' => true]; });
