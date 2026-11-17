# install.ps1: the one-line install for Windows (PowerShell 5.1 and PowerShell 7).
#
#   irm <raw url>/install.ps1 | iex
#
# A thin entry point, like install.sh: it checks Docker and Git, fetches the source into
# $AURA_HOME\src and hands over to scripts/install.sh, which holds every install phase and runs
# under the bash that Git for Windows brings. `iex` cannot pass arguments, so everything comes
# from the environment, set before the line above:
#
#   $env:AURA_REPO_URL      where the source is fetched from (the release sets the default below)
#   $env:AURA_REF           branch or tag (default main)
#   $env:AURA_HOME          where to install (default $HOME\aura); the source goes to $AURA_HOME\src
#   $env:AURA_INSTALL_ARGS  extra options for scripts/install.sh, split on spaces
#   $env:AURA_INSTALL_GIT=1 install Git for Windows with winget without asking
#
# Not covered: a path with a space inside AURA_INSTALL_ARGS, a ref change on a checkout that
# already exists (a re-run pulls the branch that is checked out), Docker itself (Docker Desktop
# must already be installed and running), and PowerShell older than 5.1.
#
# The only top-level code is one function and the call on the last line: a download cut off
# part-way is a parse error or never reaches the call, so it runs nothing. Keep the call last.
function Install-Aura {
    # The release sets this to the public repository; a fork or a mirror sets AURA_REPO_URL instead.
    $AURA_REPO_DEFAULT = "https://github.com/ChaseSunstrom/aura"
    # Set inside the function: run through iex this would otherwise change the caller's session.
    $ErrorActionPreference = 'Stop'

    function Say([string]$Message) { Write-Host "[aura] $Message" }

    # A native program run with errors not fatal: under 'Stop', Windows PowerShell 5.1 turns a
    # line a native program writes to stderr into a terminating error, so git's progress would
    # abort the install. The caller reads the exit code.
    function Invoke-Native([scriptblock]$Block, [switch]$Quiet) {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = 0
        try {
            if ($Quiet) { & $Block *> $null } else { & $Block | Out-Host }
            return $global:LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previous
        }
    }

    function Confirm-Install([string]$Question) {
        try { $answer = Read-Host "$Question [y/N]" } catch { return $false }
        return ($answer -match '^(y|yes)$')
    }

    $onWindows = ($PSVersionTable.PSVersion.Major -lt 6) -or ($IsWindows -eq $true)
    $code = 1
    try {
        $repo = if ($env:AURA_REPO_URL) { $env:AURA_REPO_URL } else { $AURA_REPO_DEFAULT }
        $ref = if ($env:AURA_REF) { $env:AURA_REF } else { 'main' }
        $homeDir = if ($env:AURA_HOME) { $env:AURA_HOME } else { Join-Path $HOME 'aura' }
        # Absolute from here on: the installer writes this path into env files compose reads,
        # and compose takes a relative bind source for a volume's name.
        $homeDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($homeDir)
        $srcDir = Join-Path $homeDir 'src'
        $extra = @()
        if ($env:AURA_INSTALL_ARGS) { $extra = @($env:AURA_INSTALL_ARGS -split '\s+' | Where-Object { $_ }) }

        # Prerequisites first, before anything is fetched. The installer's own docker calls need
        # a running Docker Desktop, so a stopped one is named here, not halfway through.
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw "docker is not installed: install Docker Desktop (https://docs.docker.com/desktop/) and start it, then run this again."
        }
        if ((Invoke-Native { docker compose version } -Quiet) -ne 0) {
            throw "the Docker Compose v2 plugin is missing (docker compose version fails): update Docker Desktop (https://docs.docker.com/desktop/)."
        }
        if ((Invoke-Native { docker info } -Quiet) -ne 0) {
            throw "the Docker daemon is not reachable (docker info fails): start Docker Desktop (https://docs.docker.com/desktop/) and run this again."
        }

        $git = Get-Command git -ErrorAction SilentlyContinue
        if (-not $git) {
            $install = ($env:AURA_INSTALL_GIT -eq '1') -or (Confirm-Install 'Git is not installed. Install Git for Windows with winget?')
            if ($install -and (Get-Command winget -ErrorAction SilentlyContinue)) {
                Say 'installing Git with winget'
                if ((Invoke-Native { winget install --id Git.Git -e --source winget }) -ne 0) {
                    throw "winget could not install Git: install Git for Windows (https://github.com/git-for-windows/git/releases) and run this again."
                }
                if ($onWindows) {
                    # winget changed the machine's PATH, not this session's.
                    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
                }
                $git = Get-Command git -ErrorAction SilentlyContinue
            }
            if (-not $git) {
                throw "git is not installed: install Git for Windows (https://github.com/git-for-windows/git/releases), which also brings the bash the installer runs under, then run this again."
            }
        }

        # Git for Windows ships its own bash.exe next to cmd\ and in its bin\. Never the
        # System32 one: that is the WSL launcher, which runs in another filesystem and sees none
        # of these paths.
        if ($onWindows) {
            $gitRoot = Split-Path -Parent (Split-Path -Parent $git.Source)
            $candidates = @((Join-Path $gitRoot 'bin\bash.exe'), (Join-Path $env:ProgramFiles 'Git\bin\bash.exe'))
            $bash = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            if (-not $bash) {
                throw "Git is installed but its bash.exe was not found next to it (looked in $($candidates -join ', ')): reinstall Git for Windows (https://github.com/git-for-windows/git/releases)."
            }
        } else {
            if (-not (Get-Command bash -ErrorAction SilentlyContinue)) {
                throw "bash is not installed: install it with your package manager."
            }
            $bash = 'bash'
        }

        New-Item -ItemType Directory -Force -Path $homeDir | Out-Null
        if (Test-Path -LiteralPath (Join-Path $srcDir '.git')) {
            Say "updating $srcDir"
            # A re-run must not fail because the network is down: the source already there is used.
            if ((Invoke-Native { git -C $srcDir pull --ff-only }) -ne 0) {
                Say "pull failed; using the source already in $srcDir"
            }
        } else {
            Say "cloning $repo ($ref) into $srcDir"
            if ((Invoke-Native { git clone --depth 1 --branch $ref $repo $srcDir }) -ne 0) {
                throw "could not clone $repo at $ref."
            }
        }
        # Forward slashes: Git's bash takes them in a Windows path, and a backslash is an escape to it.
        $installer = (Join-Path $srcDir 'scripts/install.sh') -replace '\\', '/'
        if (-not (Test-Path -LiteralPath $installer)) {
            throw "$srcDir has no scripts/install.sh: is $repo at $ref the right source?"
        }
        $dirArg = $homeDir -replace '\\', '/'

        # Without this Git's bash rewrites the /path:/path of a Docker volume argument into a
        # Windows path list and every bind mount fails.
        $env:MSYS_NO_PATHCONV = '1'
        Say "handing over to $installer"
        # Our --dir comes first so one passed in AURA_INSTALL_ARGS wins.
        & $bash $installer '--dir' $dirArg @extra
        $code = [int]$global:LASTEXITCODE
    } catch {
        Write-Host "[aura] $($_.Exception.Message)" -ForegroundColor Red
        $code = 1
    }
    # Run from a file there is a process to end with the installer's status. Run through iex,
    # `exit` would close the person's own window, so the status goes in $LASTEXITCODE instead.
    if ($PSCommandPath) { exit $code }
    $global:LASTEXITCODE = $code
}
Install-Aura
