.class public Lcom/abnsafita/protection/MyApp;
.super Landroid/app/Application;

# direct constructor
.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Landroid/app/Application;-><init>()V

    return-void
.end method

# onCreate
.method public onCreate()V
    .locals 2

    invoke-super {p0}, Landroid/app/Application;->onCreate()V

    # Log to confirm it ran
    const-string v0, "AntiCrack"
    const-string v1, ">>>>> MyApp.onCreate executed <<<<<"
    invoke-static {v0, v1}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I

    # Call your protection initialization
    invoke-static {p0}, Lcom/abnsafita/protection/ProtectionManager;->init(Landroid/content/Context;)V

    return-void
.end method