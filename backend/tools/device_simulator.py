#!/usr/bin/env python3
"""
Device Simulator CLI

Simulates IoT devices for testing the PAYGO middleware.

Commands:
- create: Register a new device
- send-metrics: Send device metrics
- activate: Activate device with token
- stress-test: Load testing

CRITICAL: All errors are displayed clearly with colors.
"""

import asyncio
import json
import random
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional
from uuid import UUID

import click
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

console = Console()

# Config file location
CONFIG_DIR = Path.home() / ".paygo-sim"
DEVICES_FILE = CONFIG_DIR / "devices.json"


def load_devices() -> dict:
    """Load saved devices from config file."""
    if not DEVICES_FILE.exists():
        return {}
    try:
        return json.loads(DEVICES_FILE.read_text())
    except Exception as e:
        console.print(f"[red]Error loading devices: {e}[/red]")
        return {}


def save_devices(devices: dict) -> None:
    """Save devices to config file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEVICES_FILE.write_text(json.dumps(devices, indent=2))


def get_device(device_id: str) -> Optional[dict]:
    """Get a saved device by ID."""
    devices = load_devices()
    return devices.get(device_id)


def save_device(device_id: str, device_data: dict) -> None:
    """Save a device."""
    devices = load_devices()
    devices[device_id] = device_data
    save_devices(devices)


class APIClient:
    """HTTP client for PAYGO API."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"X-API-Key": api_key},
        )

    async def close(self):
        await self.client.aclose()

    async def register_device(
        self,
        external_id: str,
        device_type: str,
        manufacturer: Optional[str] = None,
        model: Optional[str] = None,
    ) -> dict:
        """Register a new device."""
        response = await self.client.post(
            f"{self.base_url}/api/v1/openpaygo/device/register",
            json={
                "external_id": external_id,
                "device_type": device_type,
                "manufacturer": manufacturer,
                "model": model,
            },
        )
        response.raise_for_status()
        return response.json()

    async def send_metric(
        self,
        device_id: str,
        metric_type: str,
        value: float,
        unit: str,
    ) -> dict:
        """Send a device metric."""
        response = await self.client.post(
            f"{self.base_url}/api/v1/openpaygo/metrics",
            params={"device_id": device_id},
            json={
                "metric_type": metric_type,
                "value": value,
                "unit": unit,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        response.raise_for_status()
        return response.json()

    async def get_device(self, device_id: str) -> dict:
        """Get device info."""
        response = await self.client.get(
            f"{self.base_url}/api/v1/openpaygo/device/{device_id}",
        )
        response.raise_for_status()
        return response.json()

    async def health_check(self) -> dict:
        """Check API health."""
        response = await self.client.get(f"{self.base_url}/health")
        response.raise_for_status()
        return response.json()


@click.group()
@click.option(
    "--api-url",
    default="http://localhost:8000",
    envvar="PAYGO_API_URL",
    help="API base URL",
)
@click.option(
    "--api-key",
    default="test-api-key-for-development-only",
    envvar="PAYGO_API_KEY",
    help="API key for authentication",
)
@click.pass_context
def cli(ctx, api_url: str, api_key: str):
    """
    PAYGO Device Simulator

    Simulates IoT devices for testing the PAYGO middleware platform.
    """
    ctx.ensure_object(dict)
    ctx.obj["api_url"] = api_url
    ctx.obj["api_key"] = api_key


@cli.command()
@click.option(
    "--type",
    "device_type",
    type=click.Choice(["solar", "emobility"]),
    required=True,
    help="Device type",
)
@click.option("--id", "external_id", required=True, help="External device ID")
@click.option("--manufacturer", help="Device manufacturer")
@click.option("--model", help="Device model")
@click.pass_context
def create(ctx, device_type: str, external_id: str, manufacturer: str, model: str):
    """
    Register a new device.

    The secret key will be displayed ONCE - save it securely!
    """

    async def _create():
        client = APIClient(ctx.obj["api_url"], ctx.obj["api_key"])

        try:
            with console.status("[bold green]Registering device..."):
                result = await client.register_device(
                    external_id=external_id,
                    device_type=device_type,
                    manufacturer=manufacturer,
                    model=model,
                )

            # Display result
            console.print()
            console.print(Panel.fit(
                f"[bold green]Device Registered Successfully![/bold green]\n\n"
                f"[bold]Device ID:[/bold] {result['id']}\n"
                f"[bold]External ID:[/bold] {result['external_id']}\n"
                f"[bold]Type:[/bold] {result['device_type']}\n"
                f"[bold]Status:[/bold] {result['status']}",
                title="✅ Success",
            ))

            # Show secret key prominently
            console.print()
            console.print(Panel.fit(
                f"[bold yellow]SECRET KEY (SAVE THIS NOW!):[/bold yellow]\n\n"
                f"[bold red]{result['secret_key']}[/bold red]\n\n"
                f"[yellow]⚠️  This key will NOT be shown again![/yellow]\n"
                f"[yellow]⚠️  Store it securely - you need it for device activation![/yellow]",
                title="🔑 Secret Key",
                border_style="red",
            ))

            # Save device locally
            save_device(external_id, {
                "id": result["id"],
                "external_id": external_id,
                "device_type": device_type,
                "secret_key": result["secret_key"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

            console.print(f"\n[green]Device saved to {DEVICES_FILE}[/green]")

        except httpx.HTTPStatusError as e:
            console.print(f"[bold red]Error: {e.response.status_code}[/bold red]")
            try:
                error_detail = e.response.json()
                console.print(f"[red]{json.dumps(error_detail, indent=2)}[/red]")
            except Exception:
                console.print(f"[red]{e.response.text}[/red]")
            sys.exit(1)

        except httpx.RequestError as e:
            console.print(f"[bold red]Connection Error:[/bold red] {e}")
            console.print("[yellow]Is the API server running?[/yellow]")
            sys.exit(1)

        finally:
            await client.close()

    asyncio.run(_create())


@cli.command("send-metrics")
@click.option("--id", "device_id", required=True, help="Device external ID")
@click.option("--energy", type=float, help="Energy consumed (kWh)")
@click.option("--distance", type=float, help="Distance traveled (km)")
@click.option("--battery", type=float, help="Battery level (%)")
@click.option("--uptime", type=float, help="Uptime (hours)")
@click.option(
    "--interval",
    type=int,
    default=0,
    help="Continuous mode: seconds between sends",
)
@click.option("--count", type=int, default=1, help="Number of metrics to send")
@click.pass_context
def send_metrics(
    ctx,
    device_id: str,
    energy: Optional[float],
    distance: Optional[float],
    battery: Optional[float],
    uptime: Optional[float],
    interval: int,
    count: int,
):
    """
    Send device metrics.

    At least one metric value must be provided.
    """
    # Get saved device
    device = get_device(device_id)
    if not device:
        console.print(f"[red]Device not found locally: {device_id}[/red]")
        console.print("[yellow]Run 'create' first or check the device ID[/yellow]")
        sys.exit(1)

    # Build metrics to send
    metrics = []
    if energy is not None:
        metrics.append(("energy_consumed", energy, "kWh"))
    if distance is not None:
        metrics.append(("distance_traveled", distance, "km"))
    if battery is not None:
        metrics.append(("battery_level", battery, "%"))
    if uptime is not None:
        metrics.append(("uptime", uptime, "hours"))

    if not metrics:
        console.print("[red]Error: At least one metric must be specified[/red]")
        console.print("[yellow]Use --energy, --distance, --battery, or --uptime[/yellow]")
        sys.exit(1)

    async def _send_metrics():
        client = APIClient(ctx.obj["api_url"], ctx.obj["api_key"])

        try:
            sent = 0
            errors = 0

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:

                task = progress.add_task(
                    f"Sending metrics (0/{count})...",
                    total=count,
                )

                for i in range(count):
                    for metric_type, value, unit in metrics:
                        try:
                            result = await client.send_metric(
                                device_id=device["id"],
                                metric_type=metric_type,
                                value=value,
                                unit=unit,
                            )
                            sent += 1
                            console.print(
                                f"  [green]✓[/green] {metric_type}: {value} {unit}"
                            )

                        except httpx.HTTPStatusError as e:
                            errors += 1
                            console.print(
                                f"  [red]✗[/red] {metric_type}: {e.response.status_code}"
                            )

                    progress.update(
                        task,
                        advance=1,
                        description=f"Sending metrics ({i+1}/{count})...",
                    )

                    if interval > 0 and i < count - 1:
                        await asyncio.sleep(interval)

            # Summary
            console.print()
            if errors == 0:
                console.print(
                    f"[bold green]✅ Sent {sent} metrics successfully[/bold green]"
                )
            else:
                console.print(
                    f"[yellow]⚠️ Sent {sent} metrics, {errors} failed[/yellow]"
                )

        except httpx.RequestError as e:
            console.print(f"[bold red]Connection Error:[/bold red] {e}")
            sys.exit(1)

        finally:
            await client.close()

    asyncio.run(_send_metrics())


@cli.command()
@click.option("--id", "device_id", required=True, help="Device external ID")
@click.option("--token", required=True, help="Activation token")
@click.pass_context
def activate(ctx, device_id: str, token: str):
    """
    Simulate device activation with token.
    """
    device = get_device(device_id)
    if not device:
        console.print(f"[red]Device not found locally: {device_id}[/red]")
        sys.exit(1)

    # In a real device, this would validate the token against the secret key
    console.print(f"\n[bold]Simulating activation for device: {device_id}[/bold]")
    console.print(f"[dim]Token: {token}[/dim]")

    # Validate token format
    clean_token = token.replace("-", "")
    if len(clean_token) != 9 or not clean_token.isdigit():
        console.print("[red]❌ Invalid token format[/red]")
        console.print("[yellow]Expected format: XXX-XXX-XXX (9 digits)[/yellow]")
        sys.exit(1)

    console.print("[green]✅ Token format valid[/green]")
    console.print("[green]✅ Device activation simulated[/green]")


@cli.command("stress-test")
@click.option("--devices", type=int, default=10, help="Number of virtual devices")
@click.option("--duration", type=int, default=60, help="Test duration in seconds")
@click.option("--rate", type=float, default=1.0, help="Metrics per second per device")
@click.option("--output", type=str, help="Output CSV file for results")
@click.pass_context
def stress_test(ctx, devices: int, duration: int, rate: float, output: Optional[str]):
    """
    Run stress test with multiple virtual devices.
    """
    console.print(Panel.fit(
        f"[bold]Stress Test Configuration[/bold]\n\n"
        f"Devices: {devices}\n"
        f"Duration: {duration}s\n"
        f"Rate: {rate} metrics/sec/device\n"
        f"Total expected: {int(devices * duration * rate)} metrics",
        title="🔥 Stress Test",
    ))

    stats = {
        "requests": 0,
        "success": 0,
        "errors": 0,
        "latencies": [],
    }

    async def _stress_test():
        client = APIClient(ctx.obj["api_url"], ctx.obj["api_key"])

        # Create virtual devices
        virtual_devices = []
        with console.status("[bold green]Creating virtual devices..."):
            for i in range(devices):
                device_id = f"stress_test_{i}_{int(time.time())}"
                try:
                    result = await client.register_device(
                        external_id=device_id,
                        device_type=random.choice(["solar", "emobility"]),
                        manufacturer="StressTest",
                        model="Virtual",
                    )
                    virtual_devices.append({
                        "id": result["id"],
                        "external_id": device_id,
                    })
                except Exception as e:
                    console.print(f"[red]Failed to create device {i}: {e}[/red]")

        console.print(f"[green]Created {len(virtual_devices)} devices[/green]")

        if not virtual_devices:
            console.print("[red]No devices created - aborting[/red]")
            return

        # Run stress test
        start_time = time.time()
        interval = 1.0 / rate if rate > 0 else 1.0

        async def send_metrics_for_device(device: dict):
            while time.time() - start_time < duration:
                metric_type = random.choice([
                    "energy_consumed",
                    "distance_traveled",
                    "battery_level",
                    "uptime",
                ])
                value = random.uniform(0, 100)
                unit = "kWh" if metric_type == "energy_consumed" else "units"

                req_start = time.perf_counter()
                stats["requests"] += 1

                try:
                    await client.send_metric(
                        device_id=device["id"],
                        metric_type=metric_type,
                        value=value,
                        unit=unit,
                    )
                    stats["success"] += 1
                    latency = (time.perf_counter() - req_start) * 1000
                    stats["latencies"].append(latency)

                except Exception:
                    stats["errors"] += 1

                await asyncio.sleep(interval)

        # Run all devices concurrently
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Running stress test...", total=duration)

            async def update_progress():
                while time.time() - start_time < duration:
                    elapsed = int(time.time() - start_time)
                    progress.update(
                        task,
                        completed=elapsed,
                        description=f"Running... {stats['success']} ok, {stats['errors']} err",
                    )
                    await asyncio.sleep(1)

            tasks = [
                send_metrics_for_device(d) for d in virtual_devices
            ] + [update_progress()]

            await asyncio.gather(*tasks, return_exceptions=True)

        await client.close()

        # Results
        console.print()
        total_time = time.time() - start_time

        table = Table(title="📊 Stress Test Results")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Total Requests", str(stats["requests"]))
        table.add_row("Successful", str(stats["success"]))
        table.add_row("Failed", str(stats["errors"]))
        table.add_row(
            "Success Rate",
            f"{(stats['success'] / stats['requests'] * 100):.1f}%" if stats["requests"] > 0 else "N/A",
        )
        table.add_row(
            "Requests/sec",
            f"{stats['requests'] / total_time:.1f}",
        )

        if stats["latencies"]:
            avg_latency = sum(stats["latencies"]) / len(stats["latencies"])
            p95_latency = sorted(stats["latencies"])[int(len(stats["latencies"]) * 0.95)]
            table.add_row("Avg Latency", f"{avg_latency:.1f}ms")
            table.add_row("P95 Latency", f"{p95_latency:.1f}ms")

        console.print(table)

        # Save results if output specified
        if output:
            import csv
            with open(output, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["metric", "value"])
                writer.writerow(["total_requests", stats["requests"]])
                writer.writerow(["success", stats["success"]])
                writer.writerow(["errors", stats["errors"]])
                writer.writerow(["duration_seconds", total_time])
            console.print(f"[green]Results saved to {output}[/green]")

    asyncio.run(_stress_test())


@cli.command()
@click.pass_context
def devices(ctx):
    """
    List saved devices.
    """
    all_devices = load_devices()

    if not all_devices:
        console.print("[yellow]No devices saved locally[/yellow]")
        console.print("[dim]Use 'create' to register a new device[/dim]")
        return

    table = Table(title="Saved Devices")
    table.add_column("External ID", style="cyan")
    table.add_column("Device ID", style="dim")
    table.add_column("Type", style="green")
    table.add_column("Created", style="dim")

    for ext_id, device in all_devices.items():
        table.add_row(
            ext_id,
            device.get("id", "N/A")[:8] + "...",
            device.get("device_type", "N/A"),
            device.get("created_at", "N/A")[:10],
        )

    console.print(table)
    console.print(f"\n[dim]Config file: {DEVICES_FILE}[/dim]")


@cli.command()
@click.pass_context
def status(ctx):
    """
    Check API status.
    """

    async def _status():
        client = APIClient(ctx.obj["api_url"], ctx.obj["api_key"])

        try:
            with console.status("[bold green]Checking API status..."):
                result = await client.health_check()

            # Display status
            status_color = "green" if result["status"] == "healthy" else "red"
            console.print(Panel.fit(
                f"[bold {status_color}]Status: {result['status'].upper()}[/bold {status_color}]\n\n"
                f"[bold]Version:[/bold] {result.get('version', 'N/A')}\n"
                f"[bold]Environment:[/bold] {result.get('environment', 'N/A')}\n"
                f"[bold]API URL:[/bold] {ctx.obj['api_url']}",
                title="🏥 API Health",
            ))

            # Component status
            if "components" in result:
                table = Table(title="Components")
                table.add_column("Component", style="cyan")
                table.add_column("Status", style="green")
                table.add_column("Latency", style="dim")

                for name, comp in result["components"].items():
                    status_style = "green" if comp["status"] == "healthy" else "red"
                    table.add_row(
                        name,
                        f"[{status_style}]{comp['status']}[/{status_style}]",
                        f"{comp.get('latency_ms', 'N/A')}ms",
                    )

                console.print(table)

        except httpx.HTTPStatusError as e:
            console.print(f"[bold red]Error: {e.response.status_code}[/bold red]")
            sys.exit(1)

        except httpx.RequestError as e:
            console.print(f"[bold red]Connection Error:[/bold red] {e}")
            console.print(f"[yellow]API URL: {ctx.obj['api_url']}[/yellow]")
            console.print("[yellow]Is the API server running?[/yellow]")
            sys.exit(1)

        finally:
            await client.close()

    asyncio.run(_status())


def main():
    """Entry point for CLI."""
    cli(obj={})


if __name__ == "__main__":
    main()
