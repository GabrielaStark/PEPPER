<?php
// Fixture sintético del perfil php-apache-mysql (no es un sistema real).
use Illuminate\Support\Facades\Route;
use App\Http\Controllers\TramiteController;
use App\Http\Controllers\Auth\LoginController;

Route::get('/', function () { return redirect('/login'); });
Route::get('/login', [LoginController::class, 'showLoginForm'])->name('login');
Route::post('/login', [LoginController::class, 'login']);
Route::post('/logout', [LoginController::class, 'logout'])->name('logout');

Route::middleware(['auth'])->group(function () {
    Route::get('/tramites', [TramiteController::class, 'index'])->name('tramites.index');
    Route::get('/tramites/nuevo', [TramiteController::class, 'create'])->name('tramites.create');
    Route::post('/tramites', [TramiteController::class, 'store'])->name('tramites.store');
    Route::get('/tramites/{id}/editar', [TramiteController::class, 'edit'])->name('tramites.edit');
    Route::put('/tramites/{id}', [TramiteController::class, 'update']);
    Route::post('/tramites/{id}/turnar', 'TramiteController@turnar')->name('tramites.turnar');
    Route::delete('/tramites/{id}', [TramiteController::class, 'destroy'])->middleware('can:eliminar-tramites');
});
