<?php
namespace App\Console;

use Illuminate\Console\Scheduling\Schedule;
use Illuminate\Foundation\Console\Kernel as ConsoleKernel;

class Kernel extends ConsoleKernel
{
    protected function schedule(Schedule $schedule)
    {
        $schedule->command('tramites:vencer')->dailyAt('01:30');
        $schedule->command('tramites:recordatorios')->weekdays()->hourly();
        $schedule->job(new \App\Jobs\SincronizarPagos)->everyFifteenMinutes();
        $schedule->call(function () { \DB::table('sesiones_viejas')->delete(); })->weekly();
    }
}
