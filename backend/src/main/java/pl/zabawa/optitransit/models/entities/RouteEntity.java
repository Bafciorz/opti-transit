package pl.zabawa.optitransit.models.entities;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.*;

@Entity
@Table(name = "gtfs_routes")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
@ToString
@EqualsAndHashCode(onlyExplicitlyIncluded = true)
public class RouteEntity {

    @Id
    @EqualsAndHashCode.Include
    @Column(name = "route_id", nullable = false)
    private String id;

    @Column(name = "agency_id")
    private String agencyId;

    @Column(name = "route_short_name", nullable = false)
    private String shortName;

    @Column(name = "route_long_name")
    private String longName;

    @Column(name = "route_type", nullable = false)
    private Integer routeType;

    @Column(name = "route_color")
    private String routeColor;

    @Column(name = "route_text_color")
    private String routeTextColor;
}
