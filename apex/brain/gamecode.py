"""Scripts de DÉPART pour Unity (C#) et Unreal Engine 5 (C++), écrits pour un modèle d'`apex.brain.mesh3d`.

Gabarits déterministes (pas du texte généré par le Cerveau), réglés sur les dimensions du modèle :

- véhicule : conduite arcade (accélération, freinage, rotation — sur place pour un char), roues qui tournent ;
- arme à feu : tir par rayon (cadence, chargeur, rechargement, dégâts) ; arme blanche : frappe en zone ;
- personnage : déplacement à la troisième personne (marche, course, saut, caméra).

Ce qui est vérifié ici : le C# compile avec le vrai compilateur C# et le C++ passe la vérification de syntaxe de
g++, tous deux contre une déclaration MINIMALE des API Unity et Unreal écrite par APEX (les vraies bibliothèques ne
sont pas installables dans cet environnement) : syntaxe et cohérence prouvées, exécution dans Unity ou Unreal NON
vérifiée — à essayer dans le moteur.

Axes : un modèle d'APEX regarde vers +X (Z vers le haut). Selon l'importeur, cet axe n'est pas l'avant du moteur :
le script Unity tourne les pièces pour que l'avant du modèle (ses phares, son canon, ses pieds) soit
`transform.forward` ; les classes Unreal exposent `ForwardYawOffset` (degrés) à régler si le modèle regarde de côté.
"""

# Gabarits de code, plus lisibles avec leurs lignes d'origine (E501) ; le C++ d'Unreal s'indente par
# tabulations, sa convention (E101) ; le signe de multiplication est affiché tel quel (RUF001).
# ruff: noqa: E501, E101, RUF001

from __future__ import annotations

import re

from apex.brain.mesh3d import Model

#: Pièce qui indique l'avant de chaque modèle (centre de toutes les pièces de ce nom).
FRONT = {
    "car": "phare",
    "truck": "phare",
    "motorbike": "phare",
    "tank": "canon",
    "rifle": "canon",
    "pistol": "canon",
    "humanoid": "pied",
}


def class_base(stem: str) -> str:
    """`char-2` -> `Char2` : un identifiant valide en C# et en C++."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", stem) if w]
    base = "".join(w[:1].upper() + w[1:] for w in words) or "Modele"
    return base if base[0].isalpha() else "M" + base


def _f(value: float) -> str:
    """Nombre littéral C#/C++ (`12.5f`)."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return (text if "." in text else text + ".0") + "f"


def _category(model: Model) -> tuple[str, str]:
    category, _, kind = model.kind.partition("/")
    return category, kind


_UNITY_FACE_FORWARD = """
    // Le modèle d'APEX regarde vers ses pièces « {front} » ; selon l'importeur (OBJ, FBX, glTF) ce n'est pas
    // toujours l'avant de Unity (+Z). Les pièces sont tournées autour de l'objet pour que l'avant du modèle soit
    // transform.forward.
    static void FaceForward(Transform root, string front)
    {{
        Bounds all = new Bounds(root.position, Vector3.zero);
        Vector3 frontSum = Vector3.zero;
        int frontCount = 0;
        bool first = true;
        foreach (Renderer part in root.GetComponentsInChildren<Renderer>())
        {{
            if (first) {{ all = part.bounds; first = false; }}
            else all.Encapsulate(part.bounds);
            if (part.name.StartsWith(front)) {{ frontSum += part.bounds.center; frontCount++; }}
        }}
        if (frontCount == 0) return;
        Vector3 toFront = Vector3.ProjectOnPlane(frontSum / frontCount - all.center, root.up);
        if (toFront.sqrMagnitude < 1e-6f) return;
        float angle = Vector3.SignedAngle(toFront, root.forward, root.up);
        foreach (Transform child in root)
            child.RotateAround(root.position, root.up, angle);
    }}
"""


def unity_script(model: Model, stem: str) -> tuple[str, str]:
    """(nom du fichier .cs, code) — le nom du fichier est celui de la classe, comme Unity l'exige."""
    category, kind = _category(model)
    base = class_base(stem)
    p = model.parameters
    if category == "vehicle":
        cls = f"Apex{base}Controller"
        L, W, H = p["length"], p["width"], p["height"]
        tank = kind == "tank"
        mass = (
            {"car": 1300.0, "truck": 9000.0, "tank": 45000.0, "motorbike": 220.0}[kind]
            * (L * W * H)
            / {
                "car": 4.3 * 1.8 * 1.45,
                "truck": 7.5 * 2.5 * 3.2,
                "tank": 7.0 * 3.4 * 2.4,
                "motorbike": 2.1 * 0.8 * 1.15,
            }[kind]
        )
        vmax = {"car": 40.0, "truck": 25.0, "tank": 15.0, "motorbike": 45.0}[kind]
        accel = {"car": 6.0, "truck": 2.5, "tank": 2.0, "motorbike": 8.0}[kind]
        turn = {"car": 60.0, "truck": 35.0, "tank": 40.0, "motorbike": 70.0}[kind]
        radius = H * (
            0.42 * 0.42 * 0.8 if tank else 0.28 if kind == "motorbike" else 0.24 if kind == "car" else 0.17
        )
        code = f"""// Généré par APEX pour « {model.name} » ({L:g} × {W:g} × {H:g} m, {p["wheels"]} roues).
// Script de DÉPART pour Unity (C#), à adapter. Installation : importer {stem}.obj (ou le .fbx produit par
// {stem}_blender.py), le placer dans la scène, ajouter ce script sur l'objet racine (un Rigidbody est ajouté),
// donner un Collider au véhicule et au sol, puis jouer : axes « Vertical » et « Horizontal » (flèches, ZQSD/WASD).
// Vérifié par APEX : compilation C# contre une déclaration minimale de l'API Unity ; non exécuté dans Unity.
using System.Collections.Generic;
using UnityEngine;

[RequireComponent(typeof(Rigidbody))]
public class {cls} : MonoBehaviour
{{
    [Header("Conduite")]
    [Tooltip("Vitesse maximale en marche avant (m/s).")]
    public float maxSpeed = {_f(vmax)};
    [Tooltip("Accélération (m/s²).")]
    public float acceleration = {_f(accel)};
    [Tooltip("Décélération au freinage (m/s²).")]
    public float braking = {_f(accel * 2.5)};
    [Tooltip("Rotation maximale (degrés par seconde).")]
    public float turnRate = {_f(turn)};
    [Tooltip("Vrai : tourne sur place (chenilles) ; faux : tourne en roulant.")]
    public bool pivotInPlace = {"true" if tank else "false"};

    [Header("Roues : pièces « roue », « jante » ou « galet », trouvées automatiquement")]
    public List<Transform> wheels = new List<Transform>();
    public float wheelRadius = {_f(radius)};

    Rigidbody body;
    float speed;

    public float Speed {{ get {{ return speed; }} }}

    void Awake()
    {{
        body = GetComponent<Rigidbody>();
        body.mass = {_f(round(mass, 1))};
        body.interpolation = RigidbodyInterpolation.Interpolate;
        FaceForward(transform, "{FRONT[kind]}");
        if (wheels.Count == 0)
            foreach (Transform child in GetComponentsInChildren<Transform>())
                if (child.name.StartsWith("roue") || child.name.StartsWith("jante") || child.name.StartsWith("galet"))
                    wheels.Add(child);
    }}

    void FixedUpdate()
    {{
        float throttle = Input.GetAxis("Vertical");
        float steer = Input.GetAxis("Horizontal");
        float target = throttle * (throttle >= 0f ? maxSpeed : maxSpeed * 0.4f);
        bool slowing = Mathf.Abs(target) < Mathf.Abs(speed) || target * speed < 0f;
        speed = Mathf.MoveTowards(speed, target, (slowing ? braking : acceleration) * Time.fixedDeltaTime);
        float grip = pivotInPlace ? 1f : Mathf.Clamp(speed / (maxSpeed * 0.25f), -1f, 1f);
        body.MoveRotation(body.rotation * Quaternion.Euler(0f, steer * turnRate * grip * Time.fixedDeltaTime, 0f));
        body.MovePosition(body.position + transform.forward * speed * Time.fixedDeltaTime);
        float degrees = speed * Time.fixedDeltaTime / Mathf.Max(wheelRadius, 0.01f) * Mathf.Rad2Deg;
        foreach (Transform wheel in wheels)
        {{
            Renderer shape = wheel.GetComponent<Renderer>();
            Vector3 hub = shape != null ? shape.bounds.center : wheel.position;
            wheel.RotateAround(hub, transform.right, degrees);
        }}
    }}
{_UNITY_FACE_FORWARD.format(front=FRONT[kind])}}}
"""
        return f"{cls}.cs", code
    if category == "weapon":
        cls = f"Apex{base}Weapon"
        L = p["length"]
        if kind in ("rifle", "pistol"):
            rifle = kind == "rifle"
            code = f"""// Généré par APEX pour « {model.name} » ({L:g} m). Script de DÉPART pour Unity (C#), à adapter.
// Installation : importer {stem}.obj (ou le .fbx produit par {stem}_blender.py), l'attacher à la main du
// personnage ou à la caméra, ajouter ce script. Tir : « Fire1 » (clic gauche) ; rechargement : R.
// Les cibles reçoivent le message ApplyDamage(float). Vérifié par APEX : compilation C# contre une déclaration
// minimale de l'API Unity ; non exécuté dans Unity.
using UnityEngine;

public class {cls} : MonoBehaviour
{{
    [Header("Tir")]
    public float damage = {_f(34.0 if rifle else 20.0)};
    [Tooltip("Portée (m).")]
    public float range = {_f(300.0 if rifle else 50.0)};
    [Tooltip("Tirs par seconde.")]
    public float fireRate = {_f(8.0 if rifle else 3.0)};
    public int magazineSize = {30 if rifle else 12};
    [Tooltip("Durée du rechargement (s).")]
    public float reloadTime = {_f(2.2 if rifle else 1.4)};
    [Tooltip("Origine et direction du tir ; vide : la caméra principale.")]
    public Transform aim;

    int ammo;
    float nextShot;
    float reloadEnd = -1f;

    public int Ammo {{ get {{ return ammo; }} }}
    public bool Reloading {{ get {{ return reloadEnd >= 0f; }} }}

    void Awake()
    {{
        ammo = magazineSize;
    }}

    void Update()
    {{
        if (Reloading && Time.time >= reloadEnd) {{ reloadEnd = -1f; ammo = magazineSize; }}
        if (Input.GetKeyDown(KeyCode.R) && !Reloading && ammo < magazineSize) StartReload();
        if (Input.GetButton("Fire1")) Fire();
    }}

    public bool Fire()
    {{
        if (Reloading || Time.time < nextShot) return false;
        if (ammo <= 0) {{ StartReload(); return false; }}
        ammo--;
        nextShot = Time.time + 1f / Mathf.Max(fireRate, 0.01f);
        Transform from = aim != null ? aim : (Camera.main != null ? Camera.main.transform : transform);
        RaycastHit hit;
        if (Physics.Raycast(from.position, from.forward, out hit, range))
        {{
            hit.collider.SendMessageUpwards("ApplyDamage", damage, SendMessageOptions.DontRequireReceiver);
            if (hit.rigidbody != null)
                hit.rigidbody.AddForceAtPosition(from.forward * damage * 0.2f, hit.point, ForceMode.Impulse);
            Debug.DrawLine(from.position, hit.point, Color.red, 0.5f);
        }}
        return true;
    }}

    public void StartReload()
    {{
        if (!Reloading) reloadEnd = Time.time + reloadTime;
    }}
}}
"""
        else:
            sword = kind == "sword"
            code = f"""// Généré par APEX pour « {model.name} » ({L:g} m). Script de DÉPART pour Unity (C#), à adapter.
// Installation : importer {stem}.obj (ou le .fbx produit par {stem}_blender.py), l'attacher à la main du
// personnage, ajouter ce script et désigner le porteur (« holder »). Frappe : « Fire1 » (clic gauche).
// Les cibles reçoivent le message ApplyDamage(float). Vérifié par APEX : compilation C# contre une déclaration
// minimale de l'API Unity ; non exécuté dans Unity.
using UnityEngine;

public class {cls} : MonoBehaviour
{{
    [Header("Frappe")]
    public float damage = {_f(40.0 if sword else 55.0)};
    [Tooltip("Allonge (m) : longueur de l'arme.")]
    public float reach = {_f(L)};
    [Tooltip("Temps entre deux frappes (s).")]
    public float swingTime = {_f(0.6 if sword else 0.9)};
    [Tooltip("Personnage qui tient l'arme (jamais touché par sa propre frappe) ; vide : l'objet racine.")]
    public Transform holder;

    float nextSwing;

    void Update()
    {{
        if (Input.GetButtonDown("Fire1")) Swing();
    }}

    public int Swing()
    {{
        if (Time.time < nextSwing) return 0;
        nextSwing = Time.time + swingTime;
        Transform owner = holder != null ? holder : transform.root;
        Vector3 center = owner.position + owner.up * 1f + owner.forward * reach * 0.6f;
        int touched = 0;
        foreach (Collider other in Physics.OverlapSphere(center, reach * 0.6f))
        {{
            if (other.transform.IsChildOf(owner)) continue;
            other.SendMessageUpwards("ApplyDamage", damage, SendMessageOptions.DontRequireReceiver);
            touched++;
        }}
        return touched;
    }}
}}
"""
        return f"{cls}.cs", code
    cls = f"Apex{base}Character"
    H = p["height"]
    radius = H * 0.16 * {"slim": 0.85, "normal": 1.0, "strong": 1.25}[p["build"]]
    code = f"""// Généré par APEX pour « {model.name} » ({H:g} m, {p["build"]}). Script de DÉPART pour Unity (C#), à adapter.
// Installation : importer {stem}.fbx (produit par {stem}_blender.py : maillage lié à son squelette de 20 os)
// ou {stem}.obj, le placer dans la scène, ajouter ce script sur l'objet racine (un CharacterController est
// ajouté). Commandes : axes « Horizontal » / « Vertical », Maj gauche pour courir, « Jump » (espace) pour sauter.
// Vérifié par APEX : compilation C# contre une déclaration minimale de l'API Unity ; non exécuté dans Unity.
using UnityEngine;

[RequireComponent(typeof(CharacterController))]
public class {cls} : MonoBehaviour
{{
    [Header("Déplacement (m/s)")]
    public float walkSpeed = {_f(1.6 * H / 1.8)};
    public float runSpeed = {_f(4.5 * H / 1.8)};
    [Tooltip("Hauteur d'un saut (m).")]
    public float jumpHeight = {_f(0.6 * H / 1.8)};
    public float gravity = -19.6f;
    [Tooltip("Vivacité de la rotation vers la direction de marche.")]
    public float turnSmoothing = 12f;
    [Tooltip("Caméra qui oriente les déplacements ; vide : la caméra principale.")]
    public Transform cameraTransform;

    CharacterController controller;
    float verticalSpeed;

    void Awake()
    {{
        controller = GetComponent<CharacterController>();
        controller.height = {_f(H)};
        controller.radius = {_f(radius)};
        controller.center = new Vector3(0f, {_f(H / 2)}, 0f);
        FaceForward(transform, "{FRONT["humanoid"]}");
        if (cameraTransform == null && Camera.main != null) cameraTransform = Camera.main.transform;
    }}

    void Update()
    {{
        Vector3 input = Vector3.ClampMagnitude(new Vector3(Input.GetAxis("Horizontal"), 0f, Input.GetAxis("Vertical")), 1f);
        Vector3 move = input;
        if (cameraTransform != null)
        {{
            Vector3 forward = Vector3.ProjectOnPlane(cameraTransform.forward, Vector3.up).normalized;
            Vector3 right = Vector3.Cross(Vector3.up, forward);
            move = forward * input.z + right * input.x;
        }}
        float speed = Input.GetKey(KeyCode.LeftShift) ? runSpeed : walkSpeed;
        if (move.sqrMagnitude > 1e-4f)
            transform.rotation = Quaternion.Slerp(transform.rotation, Quaternion.LookRotation(move), turnSmoothing * Time.deltaTime);
        if (controller.isGrounded && verticalSpeed < 0f) verticalSpeed = -2f;
        if (controller.isGrounded && Input.GetButtonDown("Jump")) verticalSpeed = Mathf.Sqrt(jumpHeight * -2f * gravity);
        verticalSpeed += gravity * Time.deltaTime;
        controller.Move((move * speed + Vector3.up * verticalSpeed) * Time.deltaTime);
    }}
{_UNITY_FACE_FORWARD.format(front=FRONT["humanoid"])}}}
"""
    return f"{cls}.cs", code


_UNREAL_HEADER_NOTE = """// Vérifié par APEX : syntaxe C++ (g++) contre une déclaration minimale de l'API Unreal ; non compilé ni
// exécuté dans Unreal Engine."""


def unreal_class(model: Model, stem: str) -> list[tuple[str, str]]:
    """[(nom du .h, code), (nom du .cpp, code)] — fichiers nommés d'après la classe (convention d'Unreal)."""
    category, kind = _category(model)
    base = class_base(stem)
    p = model.parameters
    if category == "vehicle":
        name = f"Apex{base}Pawn"
        cls = "A" + name
        L, W, H = p["length"] * 100, p["width"] * 100, p["height"] * 100
        tank = kind == "tank"
        vmax = {"car": 4000.0, "truck": 2500.0, "tank": 1500.0, "motorbike": 4500.0}[kind]
        accel = {"car": 600.0, "truck": 250.0, "tank": 200.0, "motorbike": 800.0}[kind]
        turn = {"car": 60.0, "truck": 35.0, "tank": 40.0, "motorbike": 70.0}[kind]
        header = f"""// Généré par APEX pour « {model.name} » ({p["length"]:g} × {p["width"]:g} × {p["height"]:g} m). Classe de DÉPART
// pour Unreal Engine 5 (C++), à adapter. Installation : copier {name}.h et {name}.cpp dans Source/<Projet>/,
// compiler, créer un Blueprint dérivé et y choisir le maillage importé ({stem}.glb) pour « Body ». Entrées
// (Paramètres du projet > Input > Axis Mappings) : MoveForward et MoveRight.
{_UNREAL_HEADER_NOTE}
#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"
#include "{name}.generated.h"

class UBoxComponent;
class UStaticMeshComponent;

UCLASS()
class {cls} : public APawn
{{
	GENERATED_BODY()

public:
	{cls}();

	virtual void Tick(float DeltaSeconds) override;
	virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;

	/** Boîte de collision aux dimensions du modèle (cm). */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "APEX")
	UBoxComponent* Collision;

	/** Maillage importé (choisi dans le Blueprint). */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "APEX")
	UStaticMeshComponent* Body;

	/** Vitesse maximale en marche avant (cm/s). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Conduite")
	float MaxSpeed = {_f(vmax)};

	/** Accélération (cm/s²). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Conduite")
	float Acceleration = {_f(accel)};

	/** Décélération au freinage (cm/s²). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Conduite")
	float Braking = {_f(accel * 2.5)};

	/** Rotation maximale (degrés par seconde). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Conduite")
	float TurnRate = {_f(turn)};

	/** Vrai : tourne sur place (chenilles) ; faux : tourne en roulant. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Conduite")
	bool bPivotInPlace = {"true" if tank else "false"};

	/** Si le modèle importé ne regarde pas vers +X (l'avant d'Unreal) : 90, -90 ou 180. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX")
	float ForwardYawOffset = 0.f;

	UFUNCTION(BlueprintPure, Category = "APEX")
	float GetSpeed() const {{ return Speed; }}

protected:
	virtual void OnConstruction(const FTransform& Transform) override;

private:
	void MoveForward(float Value);
	void MoveRight(float Value);

	float Throttle = 0.f;
	float Steer = 0.f;
	float Speed = 0.f;
}};
"""
        source = f"""// Généré par APEX pour « {model.name} ». Voir {name}.h.
#include "{name}.h"

#include "Components/BoxComponent.h"
#include "Components/InputComponent.h"
#include "Components/StaticMeshComponent.h"

{cls}::{cls}()
{{
	PrimaryActorTick.bCanEverTick = true;
	Collision = CreateDefaultSubobject<UBoxComponent>(TEXT("Collision"));
	Collision->SetBoxExtent(FVector({_f(L / 2)}, {_f(W / 2)}, {_f(H / 2)}));
	Collision->SetCollisionProfileName(TEXT("Pawn"));
	RootComponent = Collision;
	Body = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Body"));
	Body->SetupAttachment(Collision);
	Body->SetRelativeLocation(FVector(0.f, 0.f, {_f(-H / 2)}));
	Body->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	AutoPossessPlayer = EAutoReceiveInput::Player0;
}}

void {cls}::OnConstruction(const FTransform& Transform)
{{
	Super::OnConstruction(Transform);
	Body->SetRelativeRotation(FRotator(0.f, ForwardYawOffset, 0.f));
}}

void {cls}::Tick(float DeltaSeconds)
{{
	Super::Tick(DeltaSeconds);
	const float Target = Throttle * (Throttle >= 0.f ? MaxSpeed : MaxSpeed * 0.4f);
	const bool bSlowing = FMath::Abs(Target) < FMath::Abs(Speed) || Target * Speed < 0.f;
	Speed = FMath::FInterpConstantTo(Speed, Target, DeltaSeconds, bSlowing ? Braking : Acceleration);
	const float Grip = bPivotInPlace ? 1.f : FMath::Clamp(Speed / (MaxSpeed * 0.25f), -1.f, 1.f);
	AddActorLocalRotation(FRotator(0.f, Steer * TurnRate * Grip * DeltaSeconds, 0.f));
	FHitResult Hit;
	AddActorWorldOffset(GetActorForwardVector() * Speed * DeltaSeconds, true, &Hit);
	if (Hit.IsValidBlockingHit())
	{{
		Speed = 0.f;
	}}
}}

void {cls}::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
	PlayerInputComponent->BindAxis(TEXT("MoveForward"), this, &{cls}::MoveForward);
	PlayerInputComponent->BindAxis(TEXT("MoveRight"), this, &{cls}::MoveRight);
}}

void {cls}::MoveForward(float Value)
{{
	Throttle = FMath::Clamp(Value, -1.f, 1.f);
}}

void {cls}::MoveRight(float Value)
{{
	Steer = FMath::Clamp(Value, -1.f, 1.f);
}}
"""
        return [(f"{name}.h", header), (f"{name}.cpp", source)]
    if category == "weapon":
        name = f"Apex{base}Weapon"
        cls = "A" + name
        ranged = kind in ("rifle", "pistol")
        rifle = kind == "rifle"
        L = p["length"] * 100
        if ranged:
            api = """	/** Tire un projectile instantané (rayon) depuis From dans la direction Direction. Faux si l'arme ne peut pas
	 * tirer (cadence, chargeur vide : le rechargement démarre). */
	UFUNCTION(BlueprintCallable, Category = "APEX")
	bool Fire(const FVector& From, const FVector& Direction);

	UFUNCTION(BlueprintCallable, Category = "APEX")
	void Reload();

	UFUNCTION(BlueprintPure, Category = "APEX")
	int32 GetAmmo() const { return Ammo; }
"""
            props = f"""	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Tir")
	float Damage = {_f(34.0 if rifle else 20.0)};

	/** Portée (cm). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Tir")
	float Range = {_f(30000.0 if rifle else 5000.0)};

	/** Tirs par seconde. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Tir")
	float FireRate = {_f(8.0 if rifle else 3.0)};

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Tir")
	int32 MagazineSize = {30 if rifle else 12};

	/** Durée du rechargement (s). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Tir")
	float ReloadTime = {_f(2.2 if rifle else 1.4)};
"""
            private = """	int32 Ammo = 0;
	float NextShotTime = 0.f;
	float ReloadEndTime = -1.f;
"""
            body = f"""void {cls}::BeginPlay()
{{
	Super::BeginPlay();
	Ammo = MagazineSize;
}}

bool {cls}::Fire(const FVector& From, const FVector& Direction)
{{
	UWorld* World = GetWorld();
	if (World == nullptr)
	{{
		return false;
	}}
	const float Now = World->GetTimeSeconds();
	if (ReloadEndTime >= 0.f)
	{{
		if (Now < ReloadEndTime)
		{{
			return false;
		}}
		ReloadEndTime = -1.f;
		Ammo = MagazineSize;
	}}
	if (Now < NextShotTime)
	{{
		return false;
	}}
	if (Ammo <= 0)
	{{
		Reload();
		return false;
	}}
	--Ammo;
	NextShotTime = Now + 1.f / FMath::Max(FireRate, 0.01f);
	FCollisionQueryParams Params(SCENE_QUERY_STAT(ApexWeaponTrace), true, this);
	Params.AddIgnoredActor(GetOwner());
	const FVector To = From + Direction.GetSafeNormal() * Range;
	FHitResult Hit;
	if (World->LineTraceSingleByChannel(Hit, From, To, ECC_Visibility, Params))
	{{
		UGameplayStatics::ApplyPointDamage(Hit.GetActor(), Damage, Direction, Hit, GetInstigatorController(), this,
			UDamageType::StaticClass());
		DrawDebugLine(World, From, Hit.ImpactPoint, FColor::Red, false, 0.5f);
	}}
	return true;
}}

void {cls}::Reload()
{{
	UWorld* World = GetWorld();
	if (World != nullptr && ReloadEndTime < 0.f)
	{{
		ReloadEndTime = World->GetTimeSeconds() + ReloadTime;
	}}
}}
"""
            usage = "Tir : appeler Fire(origine, direction) depuis le personnage (par exemple la caméra)."
        else:
            api = """	/** Frappe autour de Center (zone sphérique de l'allonge de l'arme) ; rend le nombre d'acteurs touchés. */
	UFUNCTION(BlueprintCallable, Category = "APEX")
	int32 Swing(const FVector& Center);
"""
            props = f"""	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Frappe")
	float Damage = {_f(40.0 if kind == "sword" else 55.0)};

	/** Allonge (cm) : longueur de l'arme. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Frappe")
	float Reach = {_f(L)};

	/** Temps entre deux frappes (s). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Frappe")
	float SwingTime = {_f(0.6 if kind == "sword" else 0.9)};
"""
            private = """	float NextSwingTime = 0.f;
"""
            body = f"""void {cls}::BeginPlay()
{{
	Super::BeginPlay();
}}

int32 {cls}::Swing(const FVector& Center)
{{
	UWorld* World = GetWorld();
	if (World == nullptr || World->GetTimeSeconds() < NextSwingTime)
	{{
		return 0;
	}}
	NextSwingTime = World->GetTimeSeconds() + SwingTime;
	FCollisionQueryParams Params(SCENE_QUERY_STAT(ApexWeaponSwing), false, this);
	Params.AddIgnoredActor(GetOwner());
	TArray<FOverlapResult> Overlaps;
	World->OverlapMultiByChannel(Overlaps, Center, FQuat::Identity, ECC_Pawn, FCollisionShape::MakeSphere(Reach * 0.6f), Params);
	TSet<AActor*> Touched;
	for (const FOverlapResult& Overlap : Overlaps)
	{{
		AActor* Target = Overlap.GetActor();
		if (Target != nullptr && !Touched.Contains(Target))
		{{
			Touched.Add(Target);
			UGameplayStatics::ApplyDamage(Target, Damage, GetInstigatorController(), this, UDamageType::StaticClass());
		}}
	}}
	return Touched.Num();
}}
"""
            usage = "Frappe : appeler Swing(centre) depuis le personnage (par exemple devant lui, à hauteur de poitrine)."
        header = f"""// Généré par APEX pour « {model.name} » ({p["length"]:g} m). Classe de DÉPART pour Unreal Engine 5 (C++),
// à adapter. Installation : copier {name}.h et {name}.cpp dans Source/<Projet>/, compiler, créer un Blueprint
// dérivé et y choisir le maillage importé ({stem}.glb) pour « Mesh ». {usage}
{_UNREAL_HEADER_NOTE}
#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "{name}.generated.h"

class UStaticMeshComponent;

UCLASS()
class {cls} : public AActor
{{
	GENERATED_BODY()

public:
	{cls}();

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "APEX")
	UStaticMeshComponent* Mesh;

{props}
	/** Si le modèle importé ne regarde pas vers +X : 90, -90 ou 180. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX")
	float ForwardYawOffset = 0.f;

{api}
protected:
	virtual void BeginPlay() override;
	virtual void OnConstruction(const FTransform& Transform) override;

private:
{private}}};
"""
        source = f"""// Généré par APEX pour « {model.name} ». Voir {name}.h.
#include "{name}.h"

#include "Components/StaticMeshComponent.h"
#include "DrawDebugHelpers.h"
#include "Engine/World.h"
#include "GameFramework/DamageType.h"
#include "Kismet/GameplayStatics.h"

{cls}::{cls}()
{{
	Mesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Mesh"));
	RootComponent = Mesh;
	Mesh->SetCollisionEnabled(ECollisionEnabled::QueryOnly);
}}

void {cls}::OnConstruction(const FTransform& Transform)
{{
	Super::OnConstruction(Transform);
	Mesh->SetRelativeRotation(FRotator(0.f, ForwardYawOffset, 0.f));
}}

{body}"""
        return [(f"{name}.h", header), (f"{name}.cpp", source)]
    name = f"Apex{base}Character"
    cls = "A" + name
    H = p["height"] * 100
    radius = H * 0.16 * {"slim": 0.85, "normal": 1.0, "strong": 1.25}[p["build"]]
    header = f"""// Généré par APEX pour « {model.name} » ({p["height"]:g} m, {p["build"]}). Classe de DÉPART pour Unreal Engine 5
// (C++), à adapter. Installation : copier {name}.h et {name}.cpp dans Source/<Projet>/, compiler, créer un
// Blueprint dérivé et y choisir le maillage squelettique importé ({stem}.glb : 20 os) pour « Mesh ». Entrées
// (Paramètres du projet > Input) : axes MoveForward, MoveRight, Turn, LookUp ; actions Jump et Run.
{_UNREAL_HEADER_NOTE}
#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Character.h"
#include "{name}.generated.h"

class UCameraComponent;
class USpringArmComponent;

UCLASS()
class {cls} : public ACharacter
{{
	GENERATED_BODY()

public:
	{cls}();

	virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "APEX")
	USpringArmComponent* CameraBoom;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "APEX")
	UCameraComponent* FollowCamera;

	/** Vitesse de marche (cm/s). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Déplacement")
	float WalkSpeed = {_f(160.0 * H / 180)};

	/** Vitesse de course (cm/s). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX|Déplacement")
	float RunSpeed = {_f(450.0 * H / 180)};

	/** Si le modèle importé ne regarde pas vers +X : 90, -90 ou 180. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "APEX")
	float ForwardYawOffset = 0.f;

protected:
	virtual void OnConstruction(const FTransform& Transform) override;

private:
	void MoveForward(float Value);
	void MoveRight(float Value);
	void Turn(float Value);
	void LookUp(float Value);
	void StartRun();
	void StopRun();
}};
"""
    source = f"""// Généré par APEX pour « {model.name} ». Voir {name}.h.
#include "{name}.h"

#include "Camera/CameraComponent.h"
#include "Components/CapsuleComponent.h"
#include "Components/InputComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "GameFramework/Controller.h"
#include "GameFramework/SpringArmComponent.h"

{cls}::{cls}()
{{
	GetCapsuleComponent()->InitCapsuleSize({_f(radius)}, {_f(H / 2)});
	bUseControllerRotationYaw = false;
	GetCharacterMovement()->bOrientRotationToMovement = true;
	GetCharacterMovement()->MaxWalkSpeed = WalkSpeed;
	GetCharacterMovement()->JumpZVelocity = {_f(420.0 * (H / 180) ** 0.5)};
	GetMesh()->SetRelativeLocation(FVector(0.f, 0.f, {_f(-H / 2)}));
	CameraBoom = CreateDefaultSubobject<USpringArmComponent>(TEXT("CameraBoom"));
	CameraBoom->SetupAttachment(RootComponent);
	CameraBoom->TargetArmLength = {_f(H * 1.8)};
	CameraBoom->bUsePawnControlRotation = true;
	FollowCamera = CreateDefaultSubobject<UCameraComponent>(TEXT("FollowCamera"));
	FollowCamera->SetupAttachment(CameraBoom, USpringArmComponent::SocketName);
	AutoPossessPlayer = EAutoReceiveInput::Player0;
}}

void {cls}::OnConstruction(const FTransform& Transform)
{{
	Super::OnConstruction(Transform);
	GetMesh()->SetRelativeRotation(FRotator(0.f, ForwardYawOffset, 0.f));
	GetCharacterMovement()->MaxWalkSpeed = WalkSpeed;
}}

void {cls}::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
	PlayerInputComponent->BindAxis(TEXT("MoveForward"), this, &{cls}::MoveForward);
	PlayerInputComponent->BindAxis(TEXT("MoveRight"), this, &{cls}::MoveRight);
	PlayerInputComponent->BindAxis(TEXT("Turn"), this, &{cls}::Turn);
	PlayerInputComponent->BindAxis(TEXT("LookUp"), this, &{cls}::LookUp);
	PlayerInputComponent->BindAction(TEXT("Jump"), IE_Pressed, this, &ACharacter::Jump);
	PlayerInputComponent->BindAction(TEXT("Jump"), IE_Released, this, &ACharacter::StopJumping);
	PlayerInputComponent->BindAction(TEXT("Run"), IE_Pressed, this, &{cls}::StartRun);
	PlayerInputComponent->BindAction(TEXT("Run"), IE_Released, this, &{cls}::StopRun);
}}

void {cls}::MoveForward(float Value)
{{
	if (Controller != nullptr && Value != 0.f)
	{{
		const FRotator Yaw(0.f, Controller->GetControlRotation().Yaw, 0.f);
		AddMovementInput(FRotationMatrix(Yaw).GetUnitAxis(EAxis::X), Value);
	}}
}}

void {cls}::MoveRight(float Value)
{{
	if (Controller != nullptr && Value != 0.f)
	{{
		const FRotator Yaw(0.f, Controller->GetControlRotation().Yaw, 0.f);
		AddMovementInput(FRotationMatrix(Yaw).GetUnitAxis(EAxis::Y), Value);
	}}
}}

void {cls}::Turn(float Value)
{{
	AddControllerYawInput(Value);
}}

void {cls}::LookUp(float Value)
{{
	AddControllerPitchInput(Value);
}}

void {cls}::StartRun()
{{
	GetCharacterMovement()->MaxWalkSpeed = RunSpeed;
}}

void {cls}::StopRun()
{{
	GetCharacterMovement()->MaxWalkSpeed = WalkSpeed;
}}
"""
    return [(f"{name}.h", header), (f"{name}.cpp", source)]
