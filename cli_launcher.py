import os
import sys
import subprocess
import requests
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.text import Text

from config import BOT_SERVICE

try:
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

if not os.environ.get("LC_ALL", "").endswith("UTF-8"):
    os.environ["LC_ALL"] = "en_US.UTF-8"
    os.environ["LANG"] = "en_US.UTF-8"

BACK_DIR = os.path.expanduser("~/.solobot_backup")
TEMP_DIR = os.path.expanduser("~/.solobot_tmp")
PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
GITHUB_REPO = "https://github.com/Vladless/Solo_bot"
SERVICE_NAME = BOT_SERVICE

console = Console()


def print_logo():
    logo = Text("""
███████╗ ██████╗ ██╗      ██████╗ ██████╗  ██████╗ ████████╗
██╔════╝██╔═══██╗██║     ██╔═══██╗██╔══██╗██╔═══██╗╚══██╔══╝
███████╗██║   ██║██║     ██║   ██║██████╔╝██║   ██║   ██║   
╚════██║██║   ██║██║     ██║   ██║██╔══██╗██║   ██║   ██║   
███████║╚██████╔╝███████╗╚██████╔╝██████╔╝╚██████╔╝   ██║   
╚══════╝ ╚═════╝ ╚══════╝ ╚═════╝ ╚═════╝  ╚═════╝    ╚═╝   
""", style="bold cyan")
    console.print(logo)


def backup_project():
    console.print("[yellow]Создаётся резервная копия проекта...[/yellow]")
    subprocess.run(["rm", "-rf", BACK_DIR])
    subprocess.run(["cp", "-r", PROJECT_DIR, BACK_DIR])
    console.print(f"[green]✅ Бэкап сохранён в: {BACK_DIR}[/green]")


def install_git_if_needed():
    if subprocess.run(["which", "git"], capture_output=True).returncode != 0:
        console.print("[blue]Установка Git...[/blue]")
        os.system("sudo apt update && sudo apt install -y git")


def install_dependencies():
    console.print("[blue]🔧 Установка зависимостей...[/blue]")
    os.system("source venv/bin/activate && pip install -r requirements.txt")


def restart_service():
    console.print("[blue]🚀 Перезапуск службы...[/blue]")
    os.system(f"sudo systemctl restart {SERVICE_NAME}")


def update_from_beta():
    if not Confirm.ask("[yellow]🔁 Подтвердите обновление Solobot с ветки BETA[/yellow]"):
        return

    backup_project()
    install_git_if_needed()

    os.chdir(PROJECT_DIR)
    git_dir = os.path.join(PROJECT_DIR, ".git")

    if os.path.isdir(git_dir):
        console.print("[cyan]🔄 Найден .git. Выполняется git pull...[/cyan]")
        os.system("git reset --hard")
        os.system("git pull")
    else:
        console.print("[cyan]📥 .git не найден. Клонируем репозиторий заново...[/cyan]")
        subprocess.run(["rm", "-rf", TEMP_DIR])
        if os.system(f"git clone {GITHUB_REPO} {TEMP_DIR}") != 0:
            console.print("[red]❌ Ошибка при клонировании. Обновление отменено.[/red]")
            return
        subprocess.run(["cp", "-r", f"{TEMP_DIR}/.", PROJECT_DIR])
        subprocess.run(["rm", "-rf", TEMP_DIR])

    install_dependencies()
    restart_service()
    console.print("[green]✅ Обновление с ветки BETA завершено.[/green]")


def update_from_release():
    if not Confirm.ask("[yellow]🔁 Подтвердите обновление Solobot до последнего релиза[/yellow]"):
        return

    backup_project()
    install_git_if_needed()

    try:
        response = requests.get(
            "https://api.github.com/repos/Vladless/Solo_bot/releases/latest", timeout=10
        )
        tag_name = response.json().get("tag_name")

        if not tag_name:
            raise ValueError("Не удалось получить тег релиза")

        console.print(f"[cyan]📥 Клонируем релиз {tag_name} во временную папку...[/cyan]")
        subprocess.run(["rm", "-rf", TEMP_DIR])
        if os.system(f"git clone --depth 1 --branch {tag_name} {GITHUB_REPO} {TEMP_DIR}") != 0:
            console.print("[red]❌ Ошибка при клонировании релиза. Обновление отменено.[/red]")
            return

        subprocess.run(["cp", "-r", f"{TEMP_DIR}/.", PROJECT_DIR])
        subprocess.run(["rm", "-rf", TEMP_DIR])

        install_dependencies()
        restart_service()
        console.print(f"[green]✅ Обновление до релиза {tag_name} завершено.[/green]")

    except Exception as e:
        console.print(f"[red]❌ Ошибка при обновлении: {e}[/red]")


def show_update_menu():
    table = Table(title="Выберите способ обновления", title_style="bold green")
    table.add_column("№", justify="center", style="cyan", no_wrap=True)
    table.add_column("Источник", style="white")
    table.add_row("1", "Обновить с BETA (git pull или clone)")
    table.add_row("2", "Обновить с последнего релиза (GitHub Release)")
    table.add_row("3", "Назад в меню")

    console.print(table)
    choice = Prompt.ask("[bold blue]Введите номер[/bold blue]", choices=["1", "2", "3"])

    if choice == "1":
        update_from_beta()
    elif choice == "2":
        update_from_release()


def show_menu():
    table = Table(title="Solobot CLI", title_style="bold magenta", header_style="bold blue")

    table.add_column("№", justify="center", style="cyan", no_wrap=True)
    table.add_column("Операция", style="white")

    table.add_row("1", "Запустить бота (systemd)")
    table.add_row("2", "Запустить напрямую: venv/bin/python main.py")
    table.add_row("3", "Перезапустить бота (systemd)")
    table.add_row("4", "Остановить бота (systemd)")
    table.add_row("5", "Показать логи (50 строк)")
    table.add_row("6", "Показать статус")
    table.add_row("7", "Обновить Solobot")
    table.add_row("8", "Выход")

    console.print(table)


def main():
    if os.geteuid() != 0:
        console.print("[bold red]⛔ Требуется запуск от имени root или через sudo.[/bold red]")
        sys.exit(1)
    
    os.chdir(PROJECT_DIR) 

    print_logo()

    while True:
        show_menu()
        choice = Prompt.ask("[bold blue]Введите номер действия[/bold blue]", choices=[str(i) for i in range(1, 9)])

        if choice == "1":
            os.system(f"sudo systemctl start {SERVICE_NAME}")
        elif choice == "2":
            if Confirm.ask("[green]Вы действительно хотите запустить main.py вручную?[/green]"):
                os.system("sudo venv/bin/python main.py")
        elif choice == "3":
            if Confirm.ask("[yellow]Вы действительно хотите перезапустить бота?[/yellow]"):
                os.system(f"sudo systemctl restart {SERVICE_NAME}")
        elif choice == "4":
            if Confirm.ask("[red]Вы уверены, что хотите остановить бота?[/red]"):
                os.system(f"sudo systemctl stop {SERVICE_NAME}")
        elif choice == "5":
            os.system(f"sudo journalctl -u {SERVICE_NAME} -n 50 --no-pager")
        elif choice == "6":
            os.system(f"sudo systemctl status {SERVICE_NAME}")
        elif choice == "7":
            show_update_menu()
        elif choice == "8":
            console.print("[bold cyan]👋 Выход из CLI. Удачного дня![/bold cyan]")
            break



if __name__ == "__main__":
    main()
